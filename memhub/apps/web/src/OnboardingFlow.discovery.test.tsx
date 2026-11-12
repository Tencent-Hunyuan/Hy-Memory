// @vitest-environment jsdom
import type { SourceResponse } from "@one-memory/contracts";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { completeOnboarding } from "./api";
import { OnboardingFlow } from "./OnboardingFlow";
import type { ImportSourcesController } from "./use-import-sources";

vi.mock("./api", () => ({
  completeOnboarding: vi.fn(),
  getEvolveSchedule: vi.fn().mockResolvedValue({
    enabled: true,
    time: "03:00",
  }),
  getModelProfiles: vi.fn().mockResolvedValue([]),
  setEvolveSchedule: vi.fn(),
  memoryModePlanUnsupported: vi.fn(() => false),
  getMemoryMode: vi.fn().mockResolvedValue({
    mode: "pro",
    configured_mode: "pro",
    explicitly_configured: false,
  }),
  setMemoryMode: vi.fn().mockResolvedValue({ mode: "pro" }),
  planMemoryMode: vi.fn().mockImplementation(async (target) => ({
    current: "lite",
    target,
    effective_target: target,
    capabilities: {
      content_plane: target === "lite" ? "raw" : "memory",
      generation_required: target !== "lite",
      graph_enabled: target === "ultra",
      evolve_available: target === "ultra",
    },
    effects: [],
    warnings: [],
    blockers: target === "lite" ? [] : ["active_model_required"],
  })),
  putOnboardingProgress: vi.fn().mockResolvedValue({ model_profile_id: null }),
  patchOnboardingProgress: vi.fn().mockResolvedValue({}),
}));
vi.mock("./FirstImportReview", () => ({
  defaultHostSourceIds: () => [],
  FirstImportReview: ({
    onContinue,
  }: {
    onContinue(destination: string): void;
  }) => (
    <>
      <p>Import accepted</p>
      <button type="button" onClick={() => onContinue("settings")}>
        进入第三步
      </button>
    </>
  ),
}));
vi.mock("./OnboardingModelController", () => ({
  OnboardingModelController: ({ onBack }: { onBack(): void }) => (
    <>
      <h1>第三步模型页面</h1>
      <button type="button" onClick={onBack}>
        返回第二步
      </button>
    </>
  ),
}));
afterEach(cleanup);

const state = {
  status: "not_started",
  selected_source_ids: [],
  started_at: null,
  completed_at: null,
  mode_intent: null,
  setup_step: null,
  model_profile_id: null,
} as const;
function source(
  id: string,
  overrides: Partial<SourceResponse> = {},
): SourceResponse {
  return {
    source_id: id,
    display_name: id,
    storage_kinds: ["jsonl"],
    available: true,
    authorized: false,
    detected: false,
    root_label: null,
    candidate_path: `/test/${id}`,
    candidate_kind: "default",
    candidate_root_id: `root-${id}`,
    location_state: "found",
    imported_messages: 0,
    imported_conversations: 0,
    sync_enabled: false,
    sync_completeness: "additions_only",
    capabilities: {
      historical_import: "implemented",
      incremental_import: "implemented",
      live_sync: "not_implemented",
      recall: "not_implemented",
      installer: "not_implemented",
      verification: "fixture_only",
    },
    ...overrides,
  };
}
function controller(sources: SourceResponse[]) {
  return {
    sources,
    jobs: [],
    loading: false,
    busyKey: null,
    error: null,
    health: null,
    healthStale: false,
    lastConnectResult: null,
    latestJobBySource: new Map(),
    refresh: vi.fn().mockResolvedValue({ sources, jobs: [], health: null }),
    detect: vi.fn(),
    startImport: vi.fn(),
    setSync: vi.fn(),
    connectForOnboarding: vi.fn().mockResolvedValue({
      accepted_source_ids: [],
      failed_sources: {},
      jobs: [],
    }),
    connectSelected: vi.fn(),
    syncAll: vi.fn(),
    changeJob: vi.fn(),
  } satisfies ImportSourcesController;
}
function view(imports: ImportSourcesController) {
  return (
    <OnboardingFlow
      state={{ ...state, selected_source_ids: [] }}
      imports={imports}
      onFinish={vi.fn()}
    />
  );
}
const checkbox = (id: string) =>
  screen.getByRole<HTMLInputElement>("checkbox", { name: `导入 ${id} 历史` });

it("moves from import to the actual third step without completing onboarding", async () => {
  const finish = vi.fn();
  vi.mocked(completeOnboarding).mockClear();
  render(
    <OnboardingFlow
      state={{ ...state, status: "importing", selected_source_ids: [] }}
      imports={controller([])}
      onFinish={finish}
    />,
  );
  await userEvent.click(screen.getByRole("button", { name: "进入第三步" }));
  await userEvent.click(screen.getByRole("radio", { name: /pro/i }));
  await userEvent.click(screen.getByRole("button", { name: "配置模型" }));
  expect(screen.getByRole("heading", { name: "第三步模型页面" })).toBeTruthy();
  expect(completeOnboarding).not.toHaveBeenCalled();
  expect(finish).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "返回第二步" }));
  expect(screen.getByRole("heading", { name: "选择记忆模式" })).toBeTruthy();
  expect(completeOnboarding).not.toHaveBeenCalled();
});

it("lets an empty first-time user enter without granting or importing a source", async () => {
  const imports = controller([]);
  const finish = vi.fn();
  vi.mocked(completeOnboarding).mockResolvedValue({
    ...state,
    selected_source_ids: [],
    status: "completed",
  });
  render(
    <OnboardingFlow
      state={{ ...state, selected_source_ids: [] }}
      imports={imports}
      onFinish={finish}
    />,
  );
  const skip = await screen.findByRole("button", { name: "选择记忆模式" });
  await waitFor(() => expect((skip as HTMLButtonElement).disabled).toBe(false));
  await userEvent.click(skip);
  await userEvent.click(screen.getByRole("radio", { name: /lite/i }));
  await userEvent.click(screen.getByRole("button", { name: "使用 Lite 进入" }));
  await waitFor(() =>
    expect(finish).toHaveBeenCalledExactlyOnceWith("sources"),
  );
  expect(imports.connectForOnboarding).not.toHaveBeenCalled();
  expect(imports.startImport).not.toHaveBeenCalled();
});

it("retains explicit retry or enter when saving the skipped onboarding fails", async () => {
  const imports = controller([]);
  const finish = vi.fn();
  vi.mocked(completeOnboarding).mockRejectedValueOnce(new Error("offline"));
  render(
    <OnboardingFlow
      state={{ ...state, selected_source_ids: [] }}
      imports={imports}
      onFinish={finish}
    />,
  );
  const skip = await screen.findByRole("button", { name: "选择记忆模式" });
  await waitFor(() => expect((skip as HTMLButtonElement).disabled).toBe(false));
  await userEvent.click(skip);
  await userEvent.click(screen.getByRole("radio", { name: /lite/i }));
  await userEvent.click(screen.getByRole("button", { name: "使用 Lite 进入" }));
  await screen.findByRole("button", { name: "重试保存并进入" });
  expect(finish).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "仍进入应用页" }));
  expect(finish).toHaveBeenCalledExactlyOnceWith("sources");
  expect(imports.connectForOnboarding).not.toHaveBeenCalled();
});

it("checks metadata automatically once in StrictMode without reading or authorizing", async () => {
  const imports = controller([
    source("workbuddy"),
    source("cursor", { available: false, location_state: "inaccessible" }),
  ]);
  render(<StrictMode>{view(imports)}</StrictMode>);
  await waitFor(() => expect(checkbox("workbuddy").checked).toBe(true));
  expect(imports.refresh).toHaveBeenCalledTimes(1);
  expect(
    screen.queryByRole("checkbox", { name: "导入 cursor 历史" }),
  ).toBeNull();
  expect(imports.detect).not.toHaveBeenCalled();
  expect(imports.connectForOnboarding).not.toHaveBeenCalled();
});

it("does not preselect a previously revoked source", async () => {
  const imports = controller([
    source("workbuddy"),
    source("codex", { authorized: false, grant_revision: 3 }),
  ]);
  render(view(imports));
  await waitFor(() => expect(checkbox("workbuddy").checked).toBe(true));
  expect(checkbox("codex").checked).toBe(false);
});

it("preserves deselection across refresh and automatically selects a newly found default source", async () => {
  const imports = controller([source("workbuddy")]);
  const rendered = render(view(imports));
  await waitFor(() => expect(checkbox("workbuddy").checked).toBe(true));
  await userEvent.click(checkbox("workbuddy"));
  await userEvent.click(screen.getByRole("button", { name: "重新检测位置" }));
  await waitFor(() => expect(imports.refresh).toHaveBeenCalledTimes(2));
  expect(checkbox("workbuddy").checked).toBe(false);
  rendered.rerender(
    view({ ...imports, sources: [source("workbuddy"), source("codex")] }),
  );
  await waitFor(() => expect(checkbox("codex").checked).toBe(true));
  expect(checkbox("workbuddy").checked).toBe(false);
});

it("does not preselect custom paths and refuses to silently reselect changed identities", async () => {
  const imports = controller([
    source("workbuddy"),
    source("codex", { candidate_kind: "environment_override" }),
  ]);
  const rendered = render(view(imports));
  await waitFor(() => expect(checkbox("workbuddy").checked).toBe(true));
  expect(checkbox("codex").checked).toBe(false);
  rendered.rerender(
    view({
      ...imports,
      sources: [
        source("workbuddy", { candidate_root_id: "changed" }),
        source("codex", { candidate_kind: "environment_override" }),
      ],
    }),
  );
  await waitFor(() => expect(checkbox("workbuddy").checked).toBe(false));
  expect(screen.getByRole("alert").textContent).toContain("身份或状态已变化");
  expect(imports.connectForOnboarding).not.toHaveBeenCalled();
});

it("freezes the explicitly submitted source list when a later source arrives", async () => {
  const imports = controller([source("workbuddy")]);
  let resolveRefresh!: (value: {
    sources: SourceResponse[];
    jobs: [];
    health: null;
  }) => void;
  const rendered = render(view(imports));
  await waitFor(() => expect(checkbox("workbuddy").checked).toBe(true));
  imports.refresh.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveRefresh = resolve;
      }),
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "开始导入" }),
  );
  const lateSources = [source("workbuddy"), source("codex")];
  rendered.rerender(view({ ...imports, sources: lateSources }));
  expect(checkbox("codex").checked).toBe(false);
  await act(async () =>
    resolveRefresh({ sources: lateSources, jobs: [], health: null }),
  );
  expect(imports.connectForOnboarding.mock.calls[0]?.[0]).toEqual([
    "workbuddy",
  ]);
});

it("reports discovery failure and permits retry without pretending nothing is installed", async () => {
  const imports = controller([source("workbuddy")]);
  imports.refresh.mockRejectedValueOnce(new Error("offline"));
  render(view(imports));
  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toContain("位置检测未完成"),
  );
  expect(checkbox("workbuddy").checked).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "重新检测位置" }));
  await waitFor(() => expect(checkbox("workbuddy").checked).toBe(true));
});
