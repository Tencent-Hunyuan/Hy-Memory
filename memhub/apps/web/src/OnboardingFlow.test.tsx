// @vitest-environment jsdom

import { webcrypto } from "node:crypto";
import type {
  ImportJob,
  OnboardingState,
  SourceResponse,
} from "@one-memory/contracts";
import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { readConnectionPreferences } from "./connection-preferences";
import { OnboardingFlow, PLUGIN_CONSENT_KEY } from "./OnboardingFlow";
import { getUiLanguage, setUiLanguage } from "./ui-language";
import {
  type ImportSourcesController,
  useImportSources,
} from "./use-import-sources";

const completeOnboarding = vi.fn();
const getModelProfiles = vi.fn();
const getMemoryProcessing = vi.fn();
const previewSourceDirectory = vi.fn();
const saveSourceDirectory = vi.fn();
const getSourceImportReceipt = vi.fn();

vi.mock("./api", () => ({
  getHealth: vi.fn(),
  getSources: vi.fn(),
  getImportJobs: vi.fn(),
  connectSources: vi.fn(),
  getSourceConnection: vi.fn(),
  createImportJob: vi.fn(),
  detectSource: vi.fn(),
  setSourceSync: vi.fn(),
  changeImportJob: vi.fn(),
  getImportJob: vi.fn(),
  apiErrorCode: vi.fn(() => null),
  completeOnboarding: (...args: unknown[]) => completeOnboarding(...args),
  getModelProfiles: (...args: unknown[]) => getModelProfiles(...args),
  getMemoryProcessing: (...args: unknown[]) => getMemoryProcessing(...args),
  previewSourceDirectory: (...args: unknown[]) =>
    previewSourceDirectory(...args),
  saveSourceDirectory: (...args: unknown[]) => saveSourceDirectory(...args),
  getSourceImportReceipt: (...args: unknown[]) =>
    getSourceImportReceipt(...args),
}));

// Keep the real import review; model execution is owned by its controller suite.
vi.mock("./OnboardingModelController", () => ({
  OnboardingModelController: ({
    onBack,
    onSkip,
    onEnabled,
  }: {
    onBack(): void;
    onSkip(): void;
    onEnabled(): void;
  }) => (
    <section aria-label="模型步骤边界">
      <button type="button" onClick={onBack}>
        返回导入
      </button>
      <button type="button" onClick={onEnabled}>
        模型启用回执已确认
      </button>
      <button type="button" onClick={onSkip}>
        跳过模型进入应用
      </button>
    </section>
  ),
}));

const onboarding: OnboardingState = {
  status: "not_started",
  selected_source_ids: [],
  started_at: null,
  completed_at: null,
};
const health = {
  status: "ok" as const,
  service: "one-memory-core" as const,
  version: "test",
  instance_id: "instance",
};

function source(
  sourceId: string,
  overrides: Partial<SourceResponse> = {},
): SourceResponse {
  const names: Record<string, string> = {
    cursor: "Cursor",
    claude: "Claude Code",
    codex: "Codex",
    workbuddy: "WorkBuddy",
  };
  return {
    source_id: sourceId,
    display_name: names[sourceId] ?? sourceId,
    storage_kinds: ["jsonl"],
    available: true,
    authorized: false,
    detected: true,
    root_label: null,
    candidate_path: `/isolated/${sourceId}`,
    candidate_kind: "default",
    candidate_root_id: `root-${sourceId}`,
    location_state: "found",
    imported_messages: 0,
    imported_conversations: 0,
    sync_enabled: false,
    capabilities: {
      historical_import: "implemented",
      incremental_import: "implemented",
      live_sync: "not_implemented",
      recall: "not_implemented",
      installer: "not_implemented",
      verification: "real_replayed",
    },
    sync_completeness: "additions_only",
    ...overrides,
  };
}

function imports(
  overrides: Partial<ImportSourcesController> = {},
): ImportSourcesController {
  const sources = overrides.sources ?? [source("claude")];
  return {
    health: null,
    healthStale: false,
    sources,
    jobs: [],
    loading: false,
    busyKey: null,
    error: null,
    lastConnectResult: null,
    latestJobBySource: new Map(),
    refresh: vi.fn().mockResolvedValue({ health, sources, jobs: [] }),
    connectForOnboarding: vi.fn().mockResolvedValue({
      accepted_source_ids: sources.map((item) => item.source_id),
      failed_sources: {},
      jobs: [],
    }),
    connectSelected: vi.fn(),
    syncAll: vi.fn(),
    setSync: vi.fn(),
    detect: vi.fn(),
    startImport: vi.fn(),
    changeJob: vi.fn(),
    ...overrides,
  };
}

const READ_CONSENT = "允许读取以上来源的本次历史";
const START = "开始导入";
const SKIP = "稍后添加，先进入";
async function openApps(_user?: ReturnType<typeof userEvent.setup>) {
  await waitFor(() =>
    expect(
      screen.getByRole("button", {
        name: "重新检测位置",
      }),
    ).toHaveProperty("disabled", false),
  );
  await act(async () => {});
}
async function skipWelcome(user?: ReturnType<typeof userEvent.setup>) {
  await openApps(user);
}
async function continueImport(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "配置模型，沉淀记忆" }));
  await user.click(screen.getByRole("button", { name: "模型启用回执已确认" }));
}
function choice(name = "Claude Code") {
  return screen.getByRole<HTMLInputElement>("checkbox", {
    name: `导入 ${name} 历史`,
  });
}
async function choose(
  user: ReturnType<typeof userEvent.setup>,
  names = ["Claude Code"],
) {
  await openApps(user);
  for (const box of screen.getAllByRole<HTMLInputElement>("checkbox", {
    name: /^导入 .* 历史$/,
  })) {
    const wanted = names.some(
      (name) => box.getAttribute("aria-label") === `导入 ${name} 历史`,
    );
    if (box.checked !== wanted) await user.click(box);
  }
}
async function review(_user: ReturnType<typeof userEvent.setup>) {
  expect(screen.queryByRole("checkbox", { name: READ_CONSENT })).toBeNull();
  expect(screen.getByRole("button", { name: START })).toHaveProperty(
    "disabled",
    false,
  );
}
async function approve(user: ReturnType<typeof userEvent.setup>) {
  await review(user);
}

describe("OnboardingFlow S1.3", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    window.localStorage.clear();
    window.oneMemory = undefined;
    completeOnboarding.mockResolvedValue({
      ...onboarding,
      status: "completed",
    });
  });
  afterEach(async () => {
    cleanup();
    window.oneMemory = undefined;
    if (getUiLanguage() !== "zh-CN") await setUiLanguage("zh-CN");
    window.localStorage.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("D41 opens source choice with metadata discovery but no content or authorization requests", async () => {
    const controller = imports();
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={vi.fn()}
      />,
    );
    expect(screen.queryByText("不必从头开始。")).toBeNull();
    for (const name of ["新会话，接着聊", "换工具，继续做", "新搭档，懂习惯"])
      expect(screen.queryByRole("tab", { name })).toBeNull();
    expect(screen.getByRole("list", { name: "候选应用" })).toBeTruthy();
    await openApps();
    expect(controller.refresh).toHaveBeenCalledOnce();
    expect(controller.detect).not.toHaveBeenCalled();
    expect(controller.startImport).not.toHaveBeenCalled();
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
    await act(async () => setUiLanguage("en"));
    expect(
      screen.getByRole("heading", { name: "Bring your context here" }),
    ).toBeTruthy();
    expect(screen.getByText("Bring your memories together")).toBeTruthy();
  });

  it("D32 keeps file-import sources out of cold start without exposing staging paths", async () => {
    const fileIds = [
      "chatgpt-export",
      "claude-export",
      "generic-export",
      "memory-content-export",
    ];
    const controller = imports({
      sources: [
        source("cursor"),
        ...fileIds.map((id) =>
          source(id, { candidate_path: `/private/import-files/ready/${id}` }),
        ),
      ],
    });
    const finish = vi.fn();
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={finish}
      />,
    );
    await skipWelcome();
    expect(screen.getAllByRole("checkbox")).toHaveLength(1);
    expect(document.body.textContent).not.toContain("import-files");
    expect(screen.queryByRole("button", { name: "导入历史包" })).toBeNull();
    expect(finish).not.toHaveBeenCalled();
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
  });

  it("R6 shows the actual selected source path before explicit submission", async () => {
    const controller = imports();
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={vi.fn()}
      />,
    );
    await skipWelcome();
    const path = screen.getByText(/\/isolated\/claude/);
    expect(path.closest("label")).toBeTruthy();
    await choose(userEvent.setup());
    await review(userEvent.setup());
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: START }));
    expect(controller.connectForOnboarding).toHaveBeenCalledExactlyOnceWith(
      ["claude"],
      [source("claude")],
    );
  });

  it("D32 accepted import stays on review and can continue without waiting for jobs", async () => {
    const controller = imports(),
      finish = vi.fn(),
      user = userEvent.setup();
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={finish}
      />,
    );
    await skipWelcome(user);
    await choose(user);
    await approve(user);
    await user.click(screen.getByRole("button", { name: START }));
    expect(
      screen.getByRole("heading", { name: "对话正在汇入，记忆从这里开始" }),
    ).toBeTruthy();
    expect(finish).not.toHaveBeenCalled();
    expect(completeOnboarding).not.toHaveBeenCalled();
    await continueImport(user);
    expect(finish).toHaveBeenCalledWith("sources");
    expect(controller.connectForOnboarding).toHaveBeenCalledOnce();
  });

  it("opens model setup immediately and preserves it across a late import failure", async () => {
    const user = userEvent.setup();
    let rejectImport!: (reason?: unknown) => void;
    const controller = imports({
      connectForOnboarding: vi.fn(
        () =>
          new Promise<
            Awaited<ReturnType<ImportSourcesController["connectForOnboarding"]>>
          >((_resolve, reject) => {
            rejectImport = reject;
          }),
      ),
    });
    window.oneMemory = {} as NonNullable<typeof window.oneMemory>;
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={vi.fn()}
      />,
    );

    await choose(user);
    await approve(user);
    await user.click(screen.getByRole("button", { name: START }));

    const continueButton = await screen.findByRole("button", {
      name: "配置模型，沉淀记忆",
    });
    expect(continueButton).toHaveProperty("disabled", false);
    await user.click(continueButton);
    const modelBoundary = screen.getByRole("region", {
      name: "模型步骤边界",
    });

    await act(async () => rejectImport(new Error("response lost")));
    expect(screen.getByRole("region", { name: "模型步骤边界" })).toBe(
      modelBoundary,
    );
    await user.click(screen.getByRole("button", { name: "返回导入" }));
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain(
        "开始导入的结果尚未确认",
      ),
    );
    expect(
      screen.getByRole("heading", { name: "对话正在汇入，记忆从这里开始" }),
    ).toBeTruthy();
  });

  it("D47 entering the same-page model step does not finish setup or grant processing", async () => {
    const finish = vi.fn();
    render(
      <OnboardingFlow
        state={{
          ...onboarding,
          status: "importing",
          selected_source_ids: ["claude"],
        }}
        imports={imports()}
        onFinish={finish}
      />,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "配置模型，沉淀记忆" }),
    );
    expect(screen.getByRole("region", { name: "模型步骤边界" })).toBeTruthy();
    expect(completeOnboarding).not.toHaveBeenCalled();
    expect(finish).not.toHaveBeenCalled();
    expect(getModelProfiles).not.toHaveBeenCalled();
  });

  it("does not trust loaded metadata or select sources before automatic discovery resolves", async () => {
    let resolve!: (value: {
      sources: SourceResponse[];
      jobs: ImportJob[];
      health: typeof health;
    }) => void;
    const controller = imports({
      sources: [source("claude"), source("cursor")],
      refresh: vi.fn(
        () =>
          new Promise<Awaited<ReturnType<ImportSourcesController["refresh"]>>>(
            (done) => {
              resolve = done;
            },
          ),
      ),
    });
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={vi.fn()}
      />,
    );
    expect(screen.getByRole("heading", { name: "本机 AI 应用" })).toBeTruthy();
    expect(screen.getAllByRole("checkbox")).toHaveLength(1);
    expect(screen.getByRole("checkbox")).toHaveProperty("disabled", true);
    expect(
      document.querySelectorAll(".onboarding-r6-skeleton").length,
    ).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: START })).toHaveProperty(
      "disabled",
      true,
    );
    expect(screen.getAllByText("已发现")).toHaveLength(1);
    expect(controller.refresh).toHaveBeenCalledOnce();
    expect(controller.detect).not.toHaveBeenCalled();
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
    await act(async () =>
      resolve({ sources: controller.sources, jobs: [], health }),
    );
    expect(choice().checked).toBe(true);
    expect(choice("Cursor").checked).toBe(true);
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
  });

  it("checks metadata once and defaults readable candidates without granting", async () => {
    const user = userEvent.setup();
    const controller = imports();
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={vi.fn()}
      />,
    );
    await openApps(user);
    expect(controller.refresh).toHaveBeenCalledOnce();
    expect(choice()).toHaveProperty("checked", true);
    expect(choice()).toHaveProperty("disabled", false);
    expect(screen.getByText("已发现")).toBeTruthy();
    for (const fn of [
      controller.detect,
      controller.startImport,
      controller.connectForOnboarding,
      completeOnboarding,
    ])
      expect(fn).not.toHaveBeenCalled();
  });

  it("omits not-found and inaccessible apps from the scan list without hiding invalid locations", async () => {
    const user = userEvent.setup();
    const controller = imports({
      sources: [
        source("workbuddy"),
        source("claude", {
          available: false,
          location_state: "not_found",
          candidate_root_id: null,
        }),
        source("cursor", {
          available: false,
          location_state: "inaccessible",
          candidate_root_id: null,
        }),
        source("codex", {
          available: false,
          location_state: "invalid",
          candidate_root_id: null,
        }),
      ],
    });
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={vi.fn()}
      />,
    );
    await openApps(user);
    await user.click(screen.getByRole("button", { name: "重新检测位置" }));
    expect(screen.getByText("已发现")).toBeTruthy();
    expect(screen.getByText("位置无效")).toBeTruthy();
    expect(screen.queryByText("未发现")).toBeNull();
    expect(screen.queryByText("无法访问")).toBeNull();
    expect(choice("WorkBuddy")).toHaveProperty("disabled", false);
    expect(choice("Codex")).toHaveProperty("disabled", true);
    expect(
      screen.queryByRole("checkbox", { name: "导入 Claude Code 历史" }),
    ).toBeNull();
    expect(
      screen.queryByRole("checkbox", { name: "导入 Cursor 历史" }),
    ).toBeNull();
    expect(screen.queryByText("未安装")).toBeNull();
    expect(screen.queryByText(/\/isolated\/claude/)).toBeNull();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it.each([null, undefined, ""])(
    "requires current Core location identity instead of granting a legacy root (%s)",
    async (root) => {
      const user = userEvent.setup();
      render(
        <OnboardingFlow
          state={onboarding}
          imports={imports({
            sources: [source("claude", { candidate_root_id: root })],
          })}
          onFinish={vi.fn()}
        />,
      );
      await openApps(user);
      await user.click(screen.getByRole("button", { name: "重新检测位置" }));
      expect(choice()).toHaveProperty("disabled", true);
      expect(screen.getAllByTitle(/Core.*位置身份/).length).toBeGreaterThan(0);
    },
  );

  it("does not offer historical import when the capability is unsupported", async () => {
    const user = userEvent.setup();
    const unsupported = source("claude");
    unsupported.capabilities.historical_import = "not_implemented";
    render(
      <OnboardingFlow
        state={onboarding}
        imports={imports({ sources: [unsupported] })}
        onFinish={vi.fn()}
      />,
    );
    await openApps(user);
    await user.click(screen.getByRole("button", { name: "重新检测位置" }));
    expect(choice()).toHaveProperty("disabled", true);
    expect(screen.getAllByTitle("暂不支持历史导入").length).toBeGreaterThan(0);
  });

  it("requires explicit start and passes only the selected identity snapshot", async () => {
    const user = userEvent.setup();
    const controller = imports({
      sources: [source("claude"), source("cursor")],
    });
    vi.mocked(controller.connectForOnboarding).mockResolvedValue({
      accepted_source_ids: ["claude"],
      failed_sources: {},
      jobs: [],
    });
    const onFinish = vi.fn();
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={onFinish}
      />,
    );
    await choose(user);
    await review(user);
    expect(screen.getByText(/\/isolated\/claude/)).toBeTruthy();
    expect(choice("Cursor").checked).toBe(false);
    expect(screen.queryByRole("checkbox", { name: READ_CONSENT })).toBeNull();
    expect(screen.getByRole("button", { name: START })).toHaveProperty(
      "disabled",
      false,
    );
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: START }));
    expect(controller.connectForOnboarding).toHaveBeenCalledWith(
      ["claude"],
      [source("claude")],
    );
    expect(controller.refresh).toHaveBeenCalledTimes(2);
    expect(completeOnboarding).not.toHaveBeenCalled();
    await continueImport(user);
    expect(completeOnboarding).toHaveBeenCalledOnce();
    expect(onFinish).toHaveBeenCalledWith("sources");
  });

  it("changing a selection before start requires a new explicit submission and preserves other choices", async () => {
    const user = userEvent.setup();
    const controller = imports({
      sources: [source("claude"), source("cursor")],
    });
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={vi.fn()}
      />,
    );
    await choose(user);
    await approve(user);
    await user.click(choice("Cursor"));
    expect(choice()).toHaveProperty("checked", true);
    await review(user);
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: START }));
    expect(controller.connectForOnboarding).toHaveBeenCalledExactlyOnceWith(
      ["claude", "cursor"],
      [source("claude"), source("cursor")],
    );
  });

  it("does not wait for a running import before entering the same-page model step", async () => {
    const user = userEvent.setup();
    const onFinish = vi.fn();
    const runningJob: ImportJob = {
      job_id: "job-1",
      user_id: "local",
      source_id: "claude",
      status: "running",
      phase: "emit",
      checkpoint: null,
      discovered: 900,
      discovered_targets: 12,
      emitted: 30,
      stored: 30,
      revised: 0,
      merged: 0,
      skipped: 0,
      errors: 0,
      created_at: "2026-09-04T00:00:01Z",
      updated_at: "2026-09-04T00:00:02Z",
    };
    render(
      <OnboardingFlow
        state={onboarding}
        imports={imports({ jobs: [runningJob] })}
        onFinish={onFinish}
      />,
    );
    await choose(user);
    await approve(user);
    await user.click(screen.getByRole("button", { name: START }));
    expect(onFinish).not.toHaveBeenCalled();
    await user.click(
      screen.getByRole("button", { name: "配置模型，沉淀记忆" }),
    );
    expect(onFinish).not.toHaveBeenCalled();
    expect(completeOnboarding).not.toHaveBeenCalled();
    await user.click(
      screen.getByRole("button", { name: "模型启用回执已确认" }),
    );
    expect(onFinish).toHaveBeenCalledWith("sources");
    expect(getModelProfiles).not.toHaveBeenCalled();
    expect(getMemoryProcessing).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("heading", { name: "配置一个模型，记忆才会生成" }),
    ).toBeNull();
  });

  it("allows skipping when no sources are known without a connection", async () => {
    const user = userEvent.setup();
    const controller = imports({ sources: [] });
    const onFinish = vi.fn();
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={onFinish}
      />,
    );
    await openApps();
    await user.click(screen.getByRole("button", { name: SKIP }));
    expect(onFinish).toHaveBeenCalledWith("sources");
    expect(completeOnboarding).toHaveBeenCalledOnce();
    expect(controller.refresh).toHaveBeenCalledOnce();
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
  });

  it("allows entering the app from step three without creating a model", async () => {
    const user = userEvent.setup();
    const controller = imports();
    const onFinish = vi.fn();
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={onFinish}
      />,
    );
    await choose(user);
    await approve(user);
    await user.click(screen.getByRole("button", { name: START }));
    await user.click(
      screen.getByRole("button", { name: "配置模型，沉淀记忆" }),
    );
    await user.click(screen.getByRole("button", { name: "跳过模型进入应用" }));
    expect(completeOnboarding).toHaveBeenCalledOnce();
    expect(onFinish).toHaveBeenCalledWith("sources");
    expect(getModelProfiles).not.toHaveBeenCalled();
    expect(getMemoryProcessing).not.toHaveBeenCalled();
  });

  it("skipping after a selection does not grant, install, sync or connect", async () => {
    const user = userEvent.setup();
    const controller = imports();
    const onFinish = vi.fn();
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={onFinish}
      />,
    );
    await choose(user);
    await user.click(screen.getByRole("button", { name: SKIP }));
    expect(onFinish).toHaveBeenCalledWith("sources");
    for (const fn of [
      controller.connectForOnboarding,
      controller.detect,
      controller.syncAll,
      controller.setSync,
    ])
      expect(fn).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(PLUGIN_CONSENT_KEY)).toBeNull();
  });

  it("keeps cold start on detected apps without a directory or package picker", async () => {
    const user = userEvent.setup();
    const controller = imports();
    const onFinish = vi.fn();
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={onFinish}
      />,
    );
    await openApps(user);
    expect(screen.queryByRole("button", { name: "选择目录" })).toBeNull();
    expect(screen.queryByLabelText("选择历史文件")).toBeNull();
    expect(screen.queryByRole("button", { name: "导入历史包" })).toBeNull();
    expect(onFinish).not.toHaveBeenCalled();
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
  });

  it("keeps metadata failures distinct from not-found and supports retry or skip", async () => {
    const user = userEvent.setup();
    const controller = imports();
    vi.mocked(controller.refresh).mockRejectedValueOnce(
      new Error("core unreachable"),
    );
    const onFinish = vi.fn();
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={onFinish}
      />,
    );
    await openApps(user);
    expect((await screen.findByRole("alert")).textContent).toContain(
      "检测未完成",
    );
    expect(choice()).toHaveProperty("disabled", true);
    expect(screen.queryByText("未发现")).toBeNull();
    await user.click(screen.getByRole("button", { name: "重新检测位置" }));
    expect(choice()).toHaveProperty("disabled", false);
    await waitFor(() => expect(choice()).toHaveProperty("checked", true));
    await user.click(screen.getByRole("button", { name: SKIP }));
    expect(onFinish).toHaveBeenCalledWith("sources");
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
  });

  it("does not grant a cached path after a pre-submit refresh failure and still allows skipping", async () => {
    const user = userEvent.setup();
    const controller = imports();
    const onFinish = vi.fn();
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={onFinish}
      />,
    );
    await choose(user);
    await approve(user);
    vi.mocked(controller.refresh).mockRejectedValueOnce(new Error("offline"));
    await user.click(screen.getByRole("button", { name: START }));
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
    expect(onFinish).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: SKIP }));
    expect(onFinish).toHaveBeenCalledWith("sources");
  });

  it("does not import a newly discovered app without an explicit selection", async () => {
    const user = userEvent.setup();
    const controller = imports();
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={vi.fn()}
      />,
    );
    await choose(user);
    await approve(user);
    vi.mocked(controller.refresh).mockResolvedValue({
      health,
      sources: [source("claude"), source("cursor")],
      jobs: [],
    });
    await user.click(screen.getByRole("button", { name: START }));
    expect(controller.connectForOnboarding).toHaveBeenCalledWith(
      ["claude"],
      [source("claude")],
    );
  });

  it("background discovery defaults new readable apps without reselecting an explicitly cleared app or authorizing", async () => {
    const user = userEvent.setup();
    const controller = imports();
    const view = render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={vi.fn()}
      />,
    );
    await choose(user);
    view.rerender(
      <OnboardingFlow
        state={onboarding}
        imports={{
          ...controller,
          sources: [source("claude"), source("cursor")],
        }}
        onFinish={vi.fn()}
      />,
    );
    expect(choice()).toHaveProperty("checked", true);
    expect(choice("Cursor")).toHaveProperty("checked", true);
    await user.click(choice());
    view.rerender(
      <OnboardingFlow
        state={onboarding}
        imports={{
          ...controller,
          sources: [source("claude"), source("cursor"), source("codex")],
        }}
        onFinish={vi.fn()}
      />,
    );
    expect(choice().checked).toBe(false);
    expect(choice("Cursor").checked).toBe(true);
    expect(choice("Codex").checked).toBe(true);
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
  });

  it("explicit recheck retains deselection and never treats it as renewed consent", async () => {
    const user = userEvent.setup();
    render(
      <OnboardingFlow
        state={onboarding}
        imports={imports()}
        onFinish={vi.fn()}
      />,
    );
    await choose(user);
    await user.click(choice());
    await user.click(screen.getByRole("button", { name: "重新检测位置" }));
    expect(choice()).toHaveProperty("checked", false);
    expect(screen.getByRole("button", { name: START })).toHaveProperty(
      "disabled",
      true,
    );
  });

  it.each([
    { candidate_path: "/another/path" },
    { candidate_revision: 1 },
    { candidate_root_id: "replaced-directory" },
    { candidate_root_id: null },
    { candidate_kind: "environment_override" as const },
    { available: false, location_state: "inaccessible" as const },
    {
      capabilities: {
        ...source("claude").capabilities,
        historical_import: "not_implemented" as const,
      },
    },
  ])("rejects scope changes at preflight (%j)", async (changed) => {
    const user = userEvent.setup();
    const controller = imports();
    const onFinish = vi.fn();
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={onFinish}
      />,
    );
    await choose(user);
    await approve(user);
    vi.mocked(controller.refresh).mockResolvedValue({
      health,
      sources: [source("claude", changed)],
      jobs: [],
    });
    await user.click(screen.getByRole("button", { name: START }));
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
    expect(onFinish).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain("已变化");
    expect(screen.queryByRole("checkbox", { name: READ_CONSENT })).toBeNull();
    expect(
      screen.queryByRole<HTMLInputElement>("checkbox", {
        name: "导入 Claude Code 历史",
      })?.checked ?? false,
    ).toBe(false);
  });

  it("background scope changes clear final consent before another submission", async () => {
    const user = userEvent.setup();
    const controller = imports();
    const view = render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={vi.fn()}
      />,
    );
    await choose(user);
    await approve(user);
    view.rerender(
      <OnboardingFlow
        state={onboarding}
        imports={{
          ...controller,
          sources: [source("claude", { candidate_root_id: "new-identity" })],
        }}
        onFinish={vi.fn()}
      />,
    );
    expect(screen.queryByRole("checkbox", { name: READ_CONSENT })).toBeNull();
    expect(choice()).toHaveProperty("checked", false);
    await user.click(choice());
    await review(user);
    expect(screen.queryByRole("checkbox", { name: READ_CONSENT })).toBeNull();
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
  });

  describe("custom location confirmation", () => {
    const custom = source("cursor", {
      candidate_kind: "environment_override",
      candidate_path: "/Volumes/ai/cursor",
    });

    it("does not include an environment override without separate location confirmation", async () => {
      const user = userEvent.setup();
      const controller = imports({ sources: [source("claude"), custom] });
      vi.mocked(controller.connectForOnboarding).mockResolvedValue({
        accepted_source_ids: ["claude"],
        failed_sources: {},
        jobs: [],
      });
      render(
        <OnboardingFlow
          state={onboarding}
          imports={controller}
          onFinish={vi.fn()}
        />,
      );
      await choose(user);
      expect(
        screen.getByText(/\/Volumes\/ai\/cursor.*自定义位置/),
      ).toBeTruthy();
      expect(choice("Cursor")).toHaveProperty("checked", false);
      await approve(user);
      await user.click(screen.getByRole("button", { name: START }));
      expect(controller.connectForOnboarding).toHaveBeenCalledWith(
        ["claude"],
        [source("claude")],
      );
    });

    it("traps and restores focus, makes background inert and cancels without selection", async () => {
      const user = userEvent.setup();
      render(
        <OnboardingFlow
          state={onboarding}
          imports={imports({ sources: [custom] })}
          onFinish={vi.fn()}
        />,
      );
      await choose(user, []);
      const trigger = choice("Cursor");
      await user.click(trigger);
      const modal = screen.getByRole("dialog", { name: "确认自定义位置" });
      const background = screen
        .getByRole("button", { name: "重新检测位置", hidden: true })
        .closest(".onboarding-r6-discovery");
      expect(background).toHaveProperty("inert", true);
      expect(modal.contains(document.activeElement)).toBe(true);
      expect(trigger.checked).toBe(false);
      expect(
        within(modal).getByRole("button", { name: "确认此位置" }),
      ).toHaveProperty("disabled", true);
      await user.tab({ shift: true });
      expect(modal.contains(document.activeElement)).toBe(true);
      await user.keyboard("{Escape}");
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(document.activeElement).toBe(trigger);
      expect(background).not.toHaveProperty("inert", true);
      expect(trigger.checked).toBe(false);
    });

    it("requires both custom location acknowledgment and final read consent", async () => {
      const user = userEvent.setup();
      const controller = imports({ sources: [source("claude"), custom] });
      render(
        <OnboardingFlow
          state={onboarding}
          imports={controller}
          onFinish={vi.fn()}
        />,
      );
      await choose(user);
      await user.click(choice("Cursor"));
      const modal = screen.getByRole("dialog");
      await user.click(
        within(modal).getByRole("checkbox", {
          name: "确认这是 Cursor 的历史位置",
        }),
      );
      await user.click(
        within(modal).getByRole("button", { name: "确认此位置" }),
      );
      expect(choice("Cursor").checked).toBe(true);
      expect(controller.connectForOnboarding).not.toHaveBeenCalled();
      await approve(user);
      await user.click(screen.getByRole("button", { name: START }));
      expect(controller.connectForOnboarding).toHaveBeenCalledWith(
        ["claude", "cursor"],
        [source("claude"), custom],
      );
    });

    it("invalidates a custom confirmation when the directory identity changes", async () => {
      const user = userEvent.setup();
      const controller = imports({ sources: [custom] });
      const onFinish = vi.fn();
      const view = render(
        <OnboardingFlow
          state={onboarding}
          imports={controller}
          onFinish={onFinish}
        />,
      );
      await choose(user, ["Cursor"]);
      await user.click(
        screen.getByRole("checkbox", { name: "确认这是 Cursor 的历史位置" }),
      );
      view.rerender(
        <OnboardingFlow
          state={onboarding}
          imports={{
            ...controller,
            sources: [{ ...custom, candidate_root_id: "replacement" }],
          }}
          onFinish={onFinish}
        />,
      );
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(choice("Cursor").checked).toBe(false);
      expect(screen.getByRole("alert").textContent).toContain("已变化");
      expect(controller.connectForOnboarding).not.toHaveBeenCalled();
    });

    it("restores focus to the source heading if the original trigger becomes unavailable", async () => {
      const user = userEvent.setup();
      const controller = imports({ sources: [custom] });
      const onFinish = vi.fn();
      const view = render(
        <OnboardingFlow
          state={onboarding}
          imports={controller}
          onFinish={onFinish}
        />,
      );
      await choose(user, ["Cursor"]);
      view.rerender(
        <OnboardingFlow
          state={onboarding}
          imports={{
            ...controller,
            sources: [
              { ...custom, available: false, location_state: "inaccessible" },
            ],
          }}
          onFinish={onFinish}
        />,
      );
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(
        screen.queryByRole("checkbox", { name: "导入 Cursor 历史" }),
      ).toBeNull();
      expect(document.activeElement).toBe(
        screen.getByRole("heading", { name: "本机 AI 应用" }),
      );
    });
  });

  it.skip("offers directory selection only on the four priority rows, outside their checkbox labels", async () => {
    vi.stubGlobal("oneMemory", { pickSourceDirectory: vi.fn() });
    const ids = [
      "workbuddy",
      "cursor",
      "claude-code",
      "codex",
      "openclaw",
      "pi",
    ];
    render(
      <OnboardingFlow
        state={onboarding}
        imports={imports({
          sources: ids.map((id) =>
            source(id, { candidate_change_allowed: true }),
          ),
        })}
        onFinish={vi.fn()}
      />,
    );
    await openApps(userEvent.setup());
    const buttons = screen.getAllByRole("button", {
      name: /^选择 .* 历史目录$/,
    });
    expect(buttons).toHaveLength(4);
    for (const button of buttons) expect(button.closest("label")).toBeNull();
    expect(
      screen.queryByRole("button", { name: "选择 openclaw 历史目录" }),
    ).toBeNull();
  });

  it.skip("saves and refreshes a candidate without selecting it, then requires location confirmation and explicit start", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("matchMedia", () => ({
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    const pick = vi
      .fn()
      .mockResolvedValue({ cancelled: false, path: "/chosen/cursor" });
    vi.stubGlobal("oneMemory", { pickSourceDirectory: pick });
    const original = source("cursor", {
      available: false,
      location_state: "not_found",
      candidate_root_id: null,
      candidate_revision: 0,
      candidate_change_allowed: true,
    });
    const changed = source("cursor", {
      candidate_path: "/normalized/cursor",
      candidate_kind: "user_selected",
      candidate_root_id: "root-new",
      candidate_revision: 1,
      candidate_change_allowed: true,
    });
    const controller = imports({ sources: [original] });
    vi.mocked(controller.refresh)
      .mockResolvedValueOnce({
        health,
        sources: [original],
        jobs: [],
      })
      .mockResolvedValue({
        health,
        sources: [changed],
        jobs: [],
      });
    previewSourceDirectory.mockResolvedValue({
      source_id: "cursor",
      path: changed.candidate_path,
      root_id: changed.candidate_root_id,
      candidate_revision: 0,
    });
    saveSourceDirectory.mockResolvedValue({
      source_id: "cursor",
      path: changed.candidate_path,
      root_id: changed.candidate_root_id,
      candidate_revision: 1,
    });
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={vi.fn()}
      />,
    );
    await openApps(user);
    await user.click(
      screen.getByRole("button", { name: "选择 Cursor 历史目录" }),
    );
    await user.click(
      await screen.findByRole("checkbox", {
        name: "仅保存此候选位置，不授权读取历史",
      }),
    );
    await user.click(screen.getByRole("button", { name: "保存候选位置" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(controller.refresh).toHaveBeenCalledTimes(2);
    expect(pick).toHaveBeenCalledExactlyOnceWith("cursor");
    expect(saveSourceDirectory).toHaveBeenCalledExactlyOnceWith("cursor", {
      path: changed.candidate_path,
      expected_revision: 0,
      expected_root_id: changed.candidate_root_id,
      user_confirmed: true,
    });
    expect(choice("Cursor").checked).toBe(false);
    expect(screen.getByText(/\/normalized\/cursor/)).toBeTruthy();
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
    expect(controller.startImport).not.toHaveBeenCalled();
    await user.click(choice("Cursor"));
    await user.click(
      screen.getByRole("checkbox", { name: "确认这是 Cursor 的历史位置" }),
    );
    await user.click(screen.getByRole("button", { name: "确认此位置" }));
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
    await approve(user);
    await user.click(screen.getByRole("button", { name: START }));
    expect(controller.connectForOnboarding).toHaveBeenCalledWith(
      ["cursor"],
      [changed],
    );
  });

  it.skip("native picker cancellation leaves an existing source selection unchanged", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("oneMemory", {
      pickSourceDirectory: vi
        .fn()
        .mockResolvedValue({ cancelled: true, path: null }),
    });
    const controller = imports({
      sources: [source("cursor", { candidate_change_allowed: true })],
    });
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={vi.fn()}
      />,
    );
    await choose(user, ["Cursor"]);
    await user.click(
      screen.getByRole("button", { name: "选择 Cursor 历史目录" }),
    );
    expect(choice("Cursor").checked).toBe(true);
    expect(controller.refresh).toHaveBeenCalledOnce();
    expect(previewSourceDirectory).not.toHaveBeenCalled();
    expect(saveSourceDirectory).not.toHaveBeenCalled();
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
    expect(controller.startImport).not.toHaveBeenCalled();
  });

  it.skip("pending directory selection locks other candidates and submit until cancellation without writes", async () => {
    let resolve!: (result: { cancelled: boolean; path: null }) => void;
    const pick = vi.fn(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    vi.stubGlobal("oneMemory", { pickSourceDirectory: pick });
    const controller = imports({
      sources: [
        source("cursor", { candidate_change_allowed: true }),
        source("codex", { candidate_change_allowed: true }),
      ],
    });
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={vi.fn()}
      />,
    );
    await openApps();
    const user = userEvent.setup();
    await user.click(
      screen.getByRole("button", { name: "选择 Cursor 历史目录" }),
    );
    for (const box of screen.getAllByRole("checkbox", { name: /^导入/ }))
      expect(box).toHaveProperty("disabled", true);
    for (const name of [
      START,
      SKIP,
      "重新检测位置",
      "选择 Codex 历史目录",
      "导入历史包",
    ])
      expect(screen.getByRole("button", { name })).toHaveProperty(
        "disabled",
        true,
      );
    await user.click(screen.getByRole("button", { name: START }));
    await user.click(
      screen.getByRole("button", { name: "选择 Codex 历史目录" }),
    );
    await user.click(screen.getByRole("button", { name: "导入历史包" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(pick).toHaveBeenCalledExactlyOnceWith("cursor");
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
    await act(async () => resolve({ cancelled: true, path: null }));
    expect(choice("Cursor").checked).toBe(true);
    expect(choice("Codex").checked).toBe(true);
    expect(screen.getByRole("button", { name: START })).toHaveProperty(
      "disabled",
      false,
    );
    expect(screen.getByRole("button", { name: "导入历史包" })).toHaveProperty(
      "disabled",
      false,
    );
    expect(controller.refresh).toHaveBeenCalledOnce();
    expect(previewSourceDirectory).not.toHaveBeenCalled();
    expect(saveSourceDirectory).not.toHaveBeenCalled();
    expect(controller.startImport).not.toHaveBeenCalled();
  });

  it.skip("rejects a native picker result after the source identity changes and releases the busy lock", async () => {
    let resolve!: (result: { cancelled: boolean; path: string }) => void;
    const pick = vi.fn(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    vi.stubGlobal("oneMemory", { pickSourceDirectory: pick });
    const original = source("cursor", { candidate_change_allowed: true });
    const controller = imports({ sources: [original] });
    const finish = vi.fn();
    const view = render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={finish}
      />,
    );
    await openApps();
    await userEvent.click(
      screen.getByRole("button", { name: "选择 Cursor 历史目录" }),
    );
    view.rerender(
      <OnboardingFlow
        state={onboarding}
        imports={{
          ...controller,
          sources: [
            {
              ...original,
              candidate_root_id: "replaced-root",
              candidate_revision: 2,
            },
          ],
        }}
        onFinish={finish}
      />,
    );
    await act(async () =>
      resolve({ cancelled: false, path: "/fixture/old-choice" }),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(previewSourceDirectory).not.toHaveBeenCalled();
    expect(saveSourceDirectory).not.toHaveBeenCalled();
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
    expect(choice("Cursor").checked).toBe(false);
    expect(screen.getByRole("button", { name: "重新检测位置" })).toHaveProperty(
      "disabled",
      false,
    );
    expect(finish).not.toHaveBeenCalled();
  });

  it.skip("does not restore an older candidate from a late post-save refresh over newer source metadata", async () => {
    const original = source("cursor", {
      candidate_change_allowed: true,
      candidate_revision: 0,
    });
    const saved = source("cursor", {
      candidate_change_allowed: true,
      candidate_revision: 1,
      candidate_kind: "user_selected",
      candidate_path: "/fixture/saved",
      candidate_root_id: "saved-root",
    });
    const newer = {
      ...saved,
      candidate_revision: 2,
      candidate_path: "/fixture/newer",
      candidate_root_id: "newer-root",
    };
    vi.stubGlobal("oneMemory", {
      pickSourceDirectory: vi
        .fn()
        .mockResolvedValue({ cancelled: false, path: saved.candidate_path }),
    });
    previewSourceDirectory.mockResolvedValue({
      source_id: "cursor",
      path: saved.candidate_path,
      root_id: saved.candidate_root_id,
      candidate_revision: 0,
    });
    saveSourceDirectory.mockResolvedValue({
      source_id: "cursor",
      path: saved.candidate_path,
      root_id: saved.candidate_root_id,
      candidate_revision: 1,
    });
    const controller = imports({ sources: [original] });
    const finish = vi.fn();
    const view = render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={finish}
      />,
    );
    await openApps();
    let resolve!: (
      value: Awaited<ReturnType<ImportSourcesController["refresh"]>>,
    ) => void;
    vi.mocked(controller.refresh).mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    const user = userEvent.setup();
    await user.click(
      screen.getByRole("button", { name: "选择 Cursor 历史目录" }),
    );
    await user.click(
      await screen.findByRole("checkbox", {
        name: "仅保存此候选位置，不授权读取历史",
      }),
    );
    await user.click(screen.getByRole("button", { name: "保存候选位置" }));
    await waitFor(() => expect(controller.refresh).toHaveBeenCalledTimes(2));
    expect(
      screen.getByRole("button", { name: START, hidden: true }),
    ).toHaveProperty("disabled", true);
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
    view.rerender(
      <OnboardingFlow
        state={onboarding}
        imports={{ ...controller, sources: [newer] }}
        onFinish={finish}
      />,
    );
    await act(async () => resolve({ health, sources: [saved], jobs: [] }));
    const row = screen
      .getByRole("checkbox", { name: "导入 Cursor 历史", hidden: true })
      .closest("li");
    expect(row?.textContent).toContain("/fixture/newer");
    expect(row?.textContent).not.toContain("/fixture/saved");
    expect(choice("Cursor").checked).toBe(false);
    expect(saveSourceDirectory).toHaveBeenCalledOnce();
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
    expect(finish).not.toHaveBeenCalled();
  });

  it.skip("ignores a native directory result after unmount without preview, save, import or navigation", async () => {
    let resolve!: (value: { cancelled: boolean; path: string }) => void;
    const pick = vi.fn(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    vi.stubGlobal("oneMemory", { pickSourceDirectory: pick });
    const controller = imports({
      sources: [source("cursor", { candidate_change_allowed: true })],
    });
    const finish = vi.fn();
    const view = render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={finish}
      />,
    );
    await openApps();
    await userEvent.click(
      screen.getByRole("button", { name: "选择 Cursor 历史目录" }),
    );
    expect(pick).toHaveBeenCalledOnce();
    view.unmount();
    await act(async () => resolve({ cancelled: false, path: "/fixture/late" }));
    expect(previewSourceDirectory).not.toHaveBeenCalled();
    expect(saveSourceDirectory).not.toHaveBeenCalled();
    expect(controller.refresh).toHaveBeenCalledOnce();
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
    expect(completeOnboarding).not.toHaveBeenCalled();
    expect(finish).not.toHaveBeenCalled();
  });

  it("clears final consent on a candidate revision change even if path and root stay the same", async () => {
    const user = userEvent.setup();
    const controller = imports();
    const view = render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={vi.fn()}
      />,
    );
    await choose(user);
    await approve(user);
    view.rerender(
      <OnboardingFlow
        state={onboarding}
        imports={{
          ...controller,
          sources: [source("claude", { candidate_revision: 2 })],
        }}
        onFinish={vi.fn()}
      />,
    );
    expect(screen.queryByRole("checkbox", { name: READ_CONSENT })).toBeNull();
    expect(choice().checked).toBe(false);
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
  });

  it("removes bundled installation intent and does not store an approval", async () => {
    const user = userEvent.setup();
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    render(
      <OnboardingFlow
        state={onboarding}
        imports={imports()}
        onFinish={vi.fn()}
      />,
    );
    expect(PLUGIN_CONSENT_KEY).toBe("one-memory.consent.plugins");
    expect(
      screen.queryByRole("checkbox", { name: "给常用 AI 装上连接" }),
    ).toBeNull();
    await choose(user);
    await approve(user);
    await user.click(screen.getByRole("button", { name: START }));
    expect(setItem).not.toHaveBeenCalledWith(
      PLUGIN_CONSENT_KEY,
      expect.anything(),
    );
    expect(window.localStorage.getItem(PLUGIN_CONSENT_KEY)).toBeNull();
  });

  it("local preference storage failure does not strand the user before import", async () => {
    const user = userEvent.setup();
    const onFinish = vi.fn();
    const controller = imports();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage unavailable", "SecurityError");
    });
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={onFinish}
      />,
    );
    await choose(user);
    await approve(user);
    await user.click(screen.getByRole("button", { name: START }));
    await continueImport(user);
    expect(onFinish).toHaveBeenCalledWith("sources");
    expect(controller.connectForOnboarding).toHaveBeenCalledOnce();
  });

  it("keeps an unknown connection visible and reconciles its receipt without a duplicate POST", async () => {
    const user = userEvent.setup();
    const onFinish = vi.fn();
    vi.stubGlobal("crypto", webcrypto);
    vi.mocked(api.getHealth).mockResolvedValue(health);
    vi.mocked(api.getSources).mockResolvedValue([
      source("claude"),
      source("cursor"),
    ]);
    vi.mocked(api.getImportJobs).mockResolvedValue([]);
    vi.mocked(api.connectSources).mockRejectedValueOnce(
      new Error("response lost"),
    );
    vi.mocked(api.getSourceConnection).mockRejectedValueOnce(
      new Error("receipt offline"),
    );
    function Harness() {
      const controller = useImportSources({ automaticSync: false });
      return controller.loading ? (
        <p>Loading fixture metadata</p>
      ) : (
        <OnboardingFlow
          state={onboarding}
          imports={controller}
          onFinish={onFinish}
        />
      );
    }
    render(<Harness />);
    await screen.findByRole("button", { name: "重新检测位置" });
    await choose(user);
    await approve(user);
    await user.click(screen.getByRole("button", { name: START }));
    await screen.findByRole("alert");
    expect(onFinish).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain("尚未确认");
    expect(completeOnboarding).not.toHaveBeenCalled();
    expect(api.connectSources).toHaveBeenCalledOnce();
    const requestId = vi.mocked(api.connectSources).mock.calls[0][2]?.requestId;
    expect(requestId).toMatch(/^[0-9a-f-]{36}$/);
    expect(api.connectSources).toHaveBeenCalledWith(
      ["claude"],
      true,
      expect.objectContaining({
        sourceRoots: { claude: "root-claude" },
        requestId,
      }),
    );
    await user.click(screen.getByRole("button", { name: START }));
    await waitFor(() =>
      expect(api.getSourceConnection).toHaveBeenCalledExactlyOnceWith(
        requestId,
      ),
    );
    expect(api.connectSources).toHaveBeenCalledOnce();
    expect(screen.getByRole("alert").textContent).toContain("尚未确认");
    vi.mocked(api.getSourceConnection).mockResolvedValueOnce({
      request_id: requestId,
      accepted_source_ids: ["claude"],
      failed_sources: {},
      jobs: [],
    });
    await openApps();
    await user.click(screen.getByRole("button", { name: START }));
    await screen.findByRole("button", { name: "配置模型，沉淀记忆" });
    await continueImport(user);
    expect(onFinish).toHaveBeenCalledWith("sources");
    expect(api.connectSources).toHaveBeenCalledOnce();
    expect(api.getSourceConnection).toHaveBeenCalledTimes(2);
    expect(api.createImportJob).not.toHaveBeenCalled();
  });

  it("locks source choices and duplicate submissions during the same intent", async () => {
    const user = userEvent.setup();
    const controller = imports();
    const onFinish = vi.fn();
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={onFinish}
      />,
    );
    await choose(user);
    await approve(user);
    let resolve = () => {};
    vi.mocked(controller.refresh).mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = () =>
            done({ health, sources: [source("claude")], jobs: [] });
        }),
    );
    await user.dblClick(screen.getByRole("button", { name: START }));
    for (const box of screen.getAllByRole("checkbox"))
      expect(box).toHaveProperty("disabled", true);
    expect(screen.getByRole("button", { name: "重新检测位置" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(screen.getByRole("button", { name: SKIP })).toHaveProperty(
      "disabled",
      true,
    );
    expect(controller.refresh).toHaveBeenCalledTimes(2);
    await act(async () => resolve());
    expect(controller.connectForOnboarding).toHaveBeenCalledOnce();
    expect(onFinish).not.toHaveBeenCalled();
    await continueImport(user);
    expect(onFinish).toHaveBeenCalledOnce();
  });

  it("locks duplicate metadata checks without simulated progress", async () => {
    const user = userEvent.setup();
    let resolve = () => {};
    const controller = imports({
      refresh: vi.fn().mockImplementation(
        () =>
          new Promise((done) => {
            resolve = () =>
              done({ health, sources: [source("claude")], jobs: [] });
          }),
      ),
    });
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={vi.fn()}
      />,
    );
    await user.dblClick(screen.getByRole("button", { name: "重新检测位置" }));
    expect(controller.refresh).toHaveBeenCalledOnce();
    expect(screen.queryByRole("progressbar")).toBeNull();
    await act(async () => resolve());
    expect(choice().checked).toBe(true);
  });

  it("rejects a stale preflight response when a newer background scope invalidated consent", async () => {
    const user = userEvent.setup();
    const controller = imports();
    const onFinish = vi.fn();
    const view = render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={onFinish}
      />,
    );
    await choose(user);
    await approve(user);
    let resolve = () => {};
    vi.mocked(controller.refresh).mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = () =>
            done({ health, sources: [source("claude")], jobs: [] });
        }),
    );
    await user.click(screen.getByRole("button", { name: START }));
    view.rerender(
      <OnboardingFlow
        state={onboarding}
        imports={{
          ...controller,
          sources: [
            source("claude", { candidate_root_id: "replaced-during-flight" }),
          ],
        }}
        onFinish={onFinish}
      />,
    );
    await act(async () => resolve());
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
    expect(onFinish).not.toHaveBeenCalled();
    expect(screen.queryByRole("checkbox", { name: READ_CONSENT })).toBeNull();
    expect(choice().checked).toBe(false);
  });

  it("does not navigate away on a late connect receipt after unmount", async () => {
    const user = userEvent.setup();
    const controller = imports();
    const onFinish = vi.fn();
    const view = render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={onFinish}
      />,
    );
    await choose(user);
    await approve(user);
    let resolve = () => {};
    vi.mocked(controller.connectForOnboarding).mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = () =>
            done({
              accepted_source_ids: ["claude"],
              failed_sources: {},
              jobs: [],
            });
        }),
    );
    await user.click(screen.getByRole("button", { name: START }));
    view.unmount();
    await act(async () => resolve());
    expect(completeOnboarding).not.toHaveBeenCalled();
    expect(onFinish).not.toHaveBeenCalled();
  });

  it("does not claim zero submission when the location changes after connect was sent", async () => {
    const user = userEvent.setup();
    const controller = imports();
    const onFinish = vi.fn();
    const view = render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={onFinish}
      />,
    );
    await choose(user);
    await approve(user);
    let resolve = () => {};
    vi.mocked(controller.connectForOnboarding).mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = () =>
            done({
              accepted_source_ids: ["claude"],
              failed_sources: {},
              jobs: [],
            });
        }),
    );
    await user.click(screen.getByRole("button", { name: START }));
    expect(controller.connectForOnboarding).toHaveBeenCalledOnce();
    view.rerender(
      <OnboardingFlow
        state={onboarding}
        imports={{
          ...controller,
          sources: [
            source("claude", { candidate_root_id: "changed-after-submit" }),
          ],
        }}
        onFinish={onFinish}
      />,
    );
    expect(screen.getByRole("alert").textContent).not.toContain("尚未提交");
    expect(screen.getByRole("alert").textContent).toContain("等待本次提交回执");
    await act(async () => resolve());
    await continueImport(user);
    expect(onFinish).toHaveBeenCalledWith("sources");
    expect(controller.connectForOnboarding).toHaveBeenCalledOnce();
  });

  it("routes partial success to per-source receipts instead of claiming all imports finished", async () => {
    const user = userEvent.setup();
    const controller = imports({
      sources: [source("claude"), source("cursor")],
    });
    vi.mocked(controller.connectForOnboarding).mockResolvedValue({
      accepted_source_ids: ["claude"],
      failed_sources: { cursor: "source_unavailable" },
      jobs: [],
    });
    const onFinish = vi.fn();
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={onFinish}
      />,
    );
    await choose(user, ["Claude Code", "Cursor"]);
    await approve(user);
    await user.click(screen.getByRole("button", { name: START }));
    expect(onFinish).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "查看未导入的应用" }));
    expect(onFinish).toHaveBeenCalledWith("sources");
  });

  it("stays in confirmation when every source failed and shows item errors", async () => {
    const user = userEvent.setup();
    const controller = imports({
      sources: [source("claude"), source("cursor")],
    });
    vi.mocked(controller.connectForOnboarding).mockResolvedValue({
      accepted_source_ids: [],
      failed_sources: {
        claude: "source_unavailable",
        cursor: "source_identity_changed",
      },
      jobs: [],
    });
    const onFinish = vi.fn();
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={onFinish}
      />,
    );
    await choose(user, ["Claude Code", "Cursor"]);
    await approve(user);
    await user.click(screen.getByRole("button", { name: START }));
    expect(onFinish).not.toHaveBeenCalled();
    expect(completeOnboarding).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: "本机 AI 应用" })).toBeTruthy();
    expect(screen.getByText(/目录暂时不可读取/)).toBeTruthy();
    expect(screen.getByText(/目录身份已变化/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: START }));
    expect(controller.connectForOnboarding).toHaveBeenLastCalledWith(
      ["claude", "cursor"],
      expect.any(Array),
      { retryFailed: true },
    );
  });

  it("keeps item errors visible when a post-connect refresh invalidated the selected location", async () => {
    const user = userEvent.setup();
    const controller = imports();
    const onFinish = vi.fn();
    const view = render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={onFinish}
      />,
    );
    await choose(user);
    await approve(user);
    let resolve = () => {};
    vi.mocked(controller.connectForOnboarding).mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = () =>
            done({
              accepted_source_ids: [],
              failed_sources: { claude: "source_identity_changed" },
              jobs: [],
            });
        }),
    );
    await user.click(screen.getByRole("button", { name: START }));
    view.rerender(
      <OnboardingFlow
        state={onboarding}
        imports={{
          ...controller,
          sources: [source("claude", { candidate_root_id: "replaced" })],
        }}
        onFinish={onFinish}
      />,
    );
    await act(async () => resolve());
    expect(
      screen.getByText("来源目录身份已变化，请返回重新检测。"),
    ).toBeTruthy();
    expect(onFinish).not.toHaveBeenCalled();
    expect(completeOnboarding).not.toHaveBeenCalled();
  });

  it("uses a known accepted receipt when post-connect refresh failed without creating again", async () => {
    const user = userEvent.setup();
    const controller = imports({ error: "最新状态暂不可读取" });
    const onFinish = vi.fn();
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={onFinish}
      />,
    );
    await choose(user);
    await approve(user);
    await user.click(screen.getByRole("button", { name: START }));
    await continueImport(user);
    expect(onFinish).toHaveBeenCalledWith("sources");
    expect(controller.connectForOnboarding).toHaveBeenCalledOnce();
    expect(controller.refresh).toHaveBeenCalledTimes(2);
  });

  it("classifies completion persistence failure separately and never retries an accepted import", async () => {
    const user = userEvent.setup();
    completeOnboarding.mockRejectedValueOnce(new Error("disk unavailable"));
    const controller = imports();
    const onFinish = vi.fn();
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={onFinish}
      />,
    );
    await choose(user);
    await approve(user);
    await user.click(screen.getByRole("button", { name: START }));
    await continueImport(user);
    // A plain Error has no HTTP status, so the outcome is unknown: the user
    // must not be told the save failed when the UI cannot prove it did.
    expect(screen.getByRole("alert").textContent).toContain("完成状态尚未确认");
    expect(screen.getByRole("alert").textContent).not.toContain(
      "完成状态未能保存",
    );
    expect(screen.queryByText(/模型失败/)).toBeNull();
    expect(onFinish).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "重试保存并进入" }));
    expect(controller.connectForOnboarding).toHaveBeenCalledOnce();
    expect(completeOnboarding).toHaveBeenCalledTimes(2);
    expect(onFinish).toHaveBeenCalledWith("sources");
  });

  it("says a definitely refused completion was not saved, not that it is unknown", async () => {
    const user = userEvent.setup();
    // 4xx is an answered refusal: this is the one branch that may say "failed".
    completeOnboarding.mockRejectedValueOnce(
      Object.assign(new Error("request failed"), {
        response: { status: 409, data: { detail: "onboarding_conflict" } },
      }),
    );
    const controller = imports();
    const onFinish = vi.fn();
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={onFinish}
      />,
    );
    await choose(user);
    await approve(user);
    await user.click(screen.getByRole("button", { name: START }));
    await continueImport(user);
    const alert = screen.getByRole("alert").textContent ?? "";
    expect(alert).toContain("完成状态未能保存");
    expect(alert).not.toContain("完成状态尚未确认");
    expect(onFinish).not.toHaveBeenCalled();
  });

  it("lets the user leave after completion cannot be persisted without reconnecting", async () => {
    const user = userEvent.setup();
    completeOnboarding.mockRejectedValue(new Error("disk unavailable"));
    const controller = imports();
    const onFinish = vi.fn();
    render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={onFinish}
      />,
    );
    await user.click(screen.getByRole("button", { name: SKIP }));
    await user.click(screen.getByRole("button", { name: "仍进入应用页" }));
    expect(onFinish).toHaveBeenCalledWith("sources");
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
    expect(completeOnboarding).toHaveBeenCalledOnce();
  });

  it("an existing importing state restores the review and exits only on user choice", async () => {
    const controller = imports();
    const onFinish = vi.fn();
    const state = {
      ...onboarding,
      status: "importing" as const,
      selected_source_ids: ["claude"],
    };
    const view = render(
      <OnboardingFlow state={state} imports={controller} onFinish={onFinish} />,
    );
    expect(
      screen.getByRole("heading", { name: "对话正在汇入，记忆从这里开始" }),
    ).toBeTruthy();
    expect(onFinish).not.toHaveBeenCalled();
    view.rerender(
      <OnboardingFlow
        state={state}
        imports={{ ...controller }}
        onFinish={onFinish}
      />,
    );
    await continueImport(userEvent.setup());
    expect(onFinish).toHaveBeenCalledOnce();
    expect(controller.refresh).not.toHaveBeenCalled();
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
  });

  it("applies checked app insertion as soon as the model step opens", async () => {
    const user = userEvent.setup();
    const connected = source("claude", {
      authorized: true,
      capabilities: {
        historical_import: "implemented",
        incremental_import: "implemented",
        live_sync: "implemented",
        recall: "implemented",
        installer: "implemented",
        verification: "real_replayed",
      },
    });
    const previewRecallInstall = vi.fn().mockResolvedValue({
      hostId: "claude",
      fingerprint: "fingerprint",
      files: [],
    });
    const permission = {
      hostId: "claude",
      state: "install-only",
      reason: null,
      receipt: {
        hostId: "claude",
        installationId: "install-1",
        revision: 1,
        status: "install-only",
        allowedTools: [],
        allowedScopes: [],
      },
    };
    const installRecall = vi.fn().mockResolvedValue({
      hostId: "claude",
      installed: true,
      permission,
    });
    const setRecallPermission = vi.fn().mockResolvedValue({
      ...permission,
      state: "approved",
      receipt: { ...permission.receipt, status: "approved" },
    });
    window.oneMemory = {
      previewRecallInstall,
      installRecall,
      getRecallPermission: vi.fn().mockResolvedValue(permission),
      setRecallPermission,
    } as unknown as NonNullable<Window["oneMemory"]>;
    const controller = imports({ sources: [connected] });
    render(
      <OnboardingFlow
        state={{
          ...onboarding,
          status: "importing",
          selected_source_ids: [connected.source_id],
        }}
        imports={controller}
        onFinish={vi.fn()}
      />,
    );
    const insertion = screen.getByRole<HTMLInputElement>("checkbox", {
      name: "一键接入应用",
    });
    await waitFor(() => expect(insertion.disabled).toBe(false));
    expect(insertion.checked).toBe(true);
    expect(readConnectionPreferences().hostSourceIds).toContain("claude");
    expect(controller.setSync).not.toHaveBeenCalled();
    expect(previewRecallInstall).not.toHaveBeenCalled();
    await user.click(
      screen.getByRole("button", { name: "配置模型，沉淀记忆" }),
    );
    expect(screen.getByRole("region", { name: "模型步骤边界" })).toBeTruthy();
    expect(controller.setSync).not.toHaveBeenCalled();
    expect(previewRecallInstall).toHaveBeenCalledWith("claude-code");
    expect(installRecall).toHaveBeenCalledWith("claude-code", "fingerprint");
    expect(setRecallPermission).toHaveBeenCalledTimes(1);
  });

  it("does not install hosts after the user unchecks one-click insertion", async () => {
    const user = userEvent.setup();
    const connected = source("claude", {
      authorized: true,
      capabilities: {
        historical_import: "implemented",
        incremental_import: "implemented",
        live_sync: "implemented",
        recall: "implemented",
        installer: "implemented",
        verification: "real_replayed",
      },
    });
    const previewRecallInstall = vi.fn();
    window.oneMemory = {
      previewRecallInstall,
      installRecall: vi.fn(),
      getRecallPermission: vi.fn(),
      setRecallPermission: vi.fn(),
    } as unknown as NonNullable<Window["oneMemory"]>;
    const controller = imports({ sources: [connected] });
    render(
      <OnboardingFlow
        state={{
          ...onboarding,
          status: "importing",
          selected_source_ids: [connected.source_id],
        }}
        imports={controller}
        onFinish={vi.fn()}
      />,
    );
    const insertion = await screen.findByRole<HTMLInputElement>("checkbox", {
      name: "一键接入应用",
    });
    await waitFor(() => expect(insertion.checked).toBe(true));
    await user.click(insertion);
    expect(insertion.checked).toBe(false);
    expect(readConnectionPreferences().hostSourceIds).toEqual([]);
    await user.click(
      screen.getByRole("button", { name: "配置模型，沉淀记忆" }),
    );
    expect(controller.setSync).not.toHaveBeenCalled();
    expect(previewRecallInstall).not.toHaveBeenCalled();
  });

  it("keeps processing enabled while disabling host integration for a test instance", async () => {
    const user = userEvent.setup();
    const connected = source("claude", {
      authorized: true,
      capabilities: {
        historical_import: "implemented",
        incremental_import: "implemented",
        live_sync: "implemented",
        recall: "implemented",
        installer: "implemented",
        verification: "real_replayed",
      },
    });
    const previewRecallInstall = vi.fn();
    window.oneMemory = {
      getConnectionSetupCapability: vi.fn().mockResolvedValue({
        available: false,
        reason: "noncanonical_data_dir",
      }),
      setUiLanguage: vi.fn(async (next: "zh-CN" | "en") => next),
      getRecallPermission: vi.fn(),
      setRecallPermission: vi.fn(),
      previewRecallInstall,
    } as unknown as NonNullable<Window["oneMemory"]>;
    const controller = imports({ sources: [connected] });
    render(
      <OnboardingFlow
        state={{
          ...onboarding,
          status: "importing",
          selected_source_ids: [connected.source_id],
        }}
        imports={controller}
        onFinish={vi.fn()}
      />,
    );

    const host = await screen.findByRole<HTMLInputElement>("checkbox", {
      name: "测试实例不接入应用",
    });
    expect(host.disabled).toBe(true);
    expect(host.checked).toBe(false);
    await act(async () => setUiLanguage("en"));
    expect(
      screen.getAllByText("Test instances do not connect to apps").length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText("测试实例不接入应用")).toBeNull();
    await user.click(
      screen.getByRole("button", {
        name: "Configure a model to create memories",
      }),
    );
    expect(controller.setSync).not.toHaveBeenCalled();
    expect(previewRecallInstall).not.toHaveBeenCalled();
  });

  it("OBS-04: says where the app has to sit when ditto left it outside Applications", async () => {
    // Installed with `ditto`, the bundle runs from a random path. The desktop
    // side reports `app_not_in_applications`; the copy already existed on the
    // application detail screen but first launch never read that reason, so
    // the cold start disabled the connection and said nothing.
    const connected = source("claude", {
      authorized: true,
      capabilities: {
        historical_import: "implemented",
        incremental_import: "implemented",
        live_sync: "implemented",
        recall: "implemented",
        installer: "implemented",
        verification: "real_replayed",
      },
    });
    window.oneMemory = {
      getConnectionSetupCapability: vi.fn().mockResolvedValue({
        available: false,
        reason: "app_not_in_applications",
      }),
      setUiLanguage: vi.fn(async (next: "zh-CN" | "en") => next),
      getRecallPermission: vi.fn(),
      setRecallPermission: vi.fn(),
    } as unknown as NonNullable<Window["oneMemory"]>;
    render(
      <OnboardingFlow
        state={{
          ...onboarding,
          status: "importing",
          selected_source_ids: [connected.source_id],
        }}
        imports={imports({ sources: [connected] })}
        onFinish={vi.fn()}
      />,
    );
    const options = await screen.findByRole("complementary", {
      name: "连接应用",
    });
    expect(
      await within(options).findByText(
        "请先把 MemHub 拖到“应用程序”并从那里重新打开，才能接入应用。",
      ),
    ).toBeTruthy();
    const host = within(options).getByRole<HTMLInputElement>("checkbox", {
      name: "测试实例不接入应用",
    });
    expect(host.disabled).toBe(true);
    expect(host.checked).toBe(false);
    await act(async () => setUiLanguage("en"));
    expect(
      within(options).getByText(
        "Move MemHub to Applications and reopen it from there before connecting apps.",
      ),
    ).toBeTruthy();
    expect(
      within(options).queryByText(
        "请先把 MemHub 拖到“应用程序”并从那里重新打开，才能接入应用。",
      ),
    ).toBeNull();
  });

  it("keeps successful one-click app connections when one host fails", async () => {
    const user = userEvent.setup();
    const a = source("claude", {
      authorized: true,
      capabilities: {
        historical_import: "implemented",
        incremental_import: "implemented",
        live_sync: "implemented",
        recall: "implemented",
        installer: "implemented",
        verification: "real_replayed",
      },
    });
    const b = source("pi", { ...a, source_id: "pi", display_name: "Pi" });
    const previewRecallInstall = vi.fn(async (id: string) => {
      if (id === "pi") throw Error("host_control_unavailable");
      return { hostId: id, fingerprint: "fingerprint", files: [] };
    });
    const permission = {
      hostId: "claude",
      state: "approved",
      reason: null,
      receipt: {
        hostId: "claude",
        installationId: "install-1",
        revision: 2,
        status: "approved",
        allowedTools: ["om_recall"],
        allowedScopes: ["all-apps"],
      },
    };
    window.oneMemory = {
      previewRecallInstall,
      installRecall: vi.fn().mockResolvedValue({
        hostId: "claude",
        installed: true,
        permission,
      }),
      getRecallPermission: vi.fn().mockResolvedValue(permission),
      setRecallPermission: vi.fn().mockResolvedValue(permission),
    } as unknown as NonNullable<Window["oneMemory"]>;
    render(
      <OnboardingFlow
        state={{
          ...onboarding,
          status: "importing",
          selected_source_ids: ["claude", "pi"],
        }}
        imports={imports({ sources: [a, b] })}
        onFinish={vi.fn()}
      />,
    );
    const insertion = await screen.findByRole<HTMLInputElement>("checkbox", {
      name: "一键接入应用",
    });
    await waitFor(() => expect(insertion.disabled).toBe(false));
    expect(previewRecallInstall).not.toHaveBeenCalled();
    expect(insertion.checked).toBe(true);
    await user.click(
      screen.getByRole("button", { name: "配置模型，沉淀记忆" }),
    );
    expect(screen.getByRole("region", { name: "模型步骤边界" })).toBeTruthy();
    expect(previewRecallInstall).toHaveBeenCalledWith("claude-code");
    expect(window.oneMemory?.installRecall).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(document.body.textContent).toMatch(
        /1 个应用已接入，1 个未完成：Pi（应用控制通道暂不可用）。/,
      ),
    );
    expect(document.body.textContent).not.toContain("Pi、Pi");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(insertion.checked).toBe(true);
  });

  it("hands off an existing import discovered while a metadata check is in flight", async () => {
    const user = userEvent.setup();
    const controller = imports();
    const onFinish = vi.fn();
    const view = render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={onFinish}
      />,
    );
    let resolve = () => {};
    vi.mocked(controller.refresh).mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = () =>
            done({ health, sources: [source("claude")], jobs: [] });
        }),
    );
    await openApps(user);
    await user.click(screen.getByRole("button", { name: "重新检测位置" }));
    view.rerender(
      <OnboardingFlow
        state={{ ...onboarding, status: "importing" }}
        imports={controller}
        onFinish={onFinish}
      />,
    );
    await act(async () => resolve());
    expect(
      screen.getByRole("heading", { name: "对话正在汇入，记忆从这里开始" }),
    ).toBeTruthy();
    expect(onFinish).not.toHaveBeenCalled();
    await continueImport(user);
    await waitFor(() => expect(onFinish).toHaveBeenCalledWith("sources"));
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
    expect(completeOnboarding).toHaveBeenCalledOnce();
  });

  it("ignores an in-flight metadata response after unmount", async () => {
    const user = userEvent.setup();
    let resolve = () => {};
    const controller = imports({
      refresh: vi.fn().mockImplementation(
        () =>
          new Promise((done) => {
            resolve = () =>
              done({ health, sources: [source("claude")], jobs: [] });
          }),
      ),
    });
    const onFinish = vi.fn();
    const view = render(
      <OnboardingFlow
        state={onboarding}
        imports={controller}
        onFinish={onFinish}
      />,
    );
    await user.click(screen.getByRole("button", { name: "重新检测位置" }));
    view.unmount();
    await act(async () => resolve());
    expect(controller.connectForOnboarding).not.toHaveBeenCalled();
    expect(onFinish).not.toHaveBeenCalled();
  });
});
