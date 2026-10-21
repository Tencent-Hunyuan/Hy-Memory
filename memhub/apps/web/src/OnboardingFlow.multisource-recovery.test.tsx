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
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as api from "./api";
import type { FirstImportReviewProps } from "./FirstImportReview";
import { OnboardingFlow } from "./OnboardingFlow";
import { useImportSources } from "./use-import-sources";

vi.mock("./api", () => ({
  getHealth: vi.fn(),
  getSources: vi.fn(),
  getImportJobs: vi.fn(),
  connectSources: vi.fn(),
  completeOnboarding: vi.fn(),
  getSourceConnection: vi.fn(),
  createImportJob: vi.fn(),
  detectSource: vi.fn(),
  setSourceSync: vi.fn(),
  changeImportJob: vi.fn(),
  getImportJob: vi.fn(),
  apiErrorCode: vi.fn(() => null),
}));
vi.mock("./FirstImportReview", () => ({
  defaultHostSourceIds: () => [],
  FirstImportReview: (props: FirstImportReviewProps) => (
    <section aria-label="导入结果">
      <output aria-label="已受理来源">{props.sourceIds.join(",")}</output>
      <output aria-label="保留任务">
        {props.jobs.map((job) => `${job.job_id}:${job.status}`).join(",")}
      </output>
      <output aria-label="失败来源">
        {Object.keys(props.failedSources ?? {}).join(",")}
      </output>
      <button type="button" onClick={() => props.onContinue("settings")}>
        继续配置模型
      </button>
      <button type="button" onClick={() => props.onContinue("sources")}>
        进入应用
      </button>
    </section>
  ),
}));
vi.mock("./OnboardingModelController", () => ({
  OnboardingModelController: ({ onBack }: { onBack(): void }) => (
    <section aria-label="模型步骤">
      <button type="button" onClick={onBack}>
        返回导入
      </button>
    </section>
  ),
}));
const state: OnboardingState = {
  status: "not_started",
  selected_source_ids: [],
  started_at: null,
  completed_at: null,
};
let sources: SourceResponse[];
let jobs: ImportJob[];
const finish = vi.fn();
function source(
  id: string,
  patch: Partial<SourceResponse> = {},
): SourceResponse {
  return {
    source_id: id,
    display_name: id,
    authorized: false,
    available: true,
    detected: false,
    storage_kinds: ["jsonl"],
    candidate_path: `/fixture/${id}`,
    candidate_kind: "default",
    candidate_root_id: `root-${id}`,
    location_state: "found",
    root_label: null,
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
    ...patch,
  };
}
function Harness() {
  const imports = useImportSources({ automaticSync: false });
  return imports.loading ? (
    <p>读取初始来源</p>
  ) : (
    <OnboardingFlow state={state} imports={imports} onFinish={finish} />
  );
}
function checkbox(id: string) {
  return screen.getByRole<HTMLInputElement>("checkbox", {
    name: `导入 ${id} 历史`,
  });
}
function count() {
  return within(
    screen.getByRole("complementary", { name: "本次导入" }),
  ).getByText(/^\d+$/).textContent;
}
async function ready() {
  render(<Harness />);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "重新检测位置" })).toHaveProperty(
      "disabled",
      false,
    ),
  );
}
function noUnrequestedWrites() {
  for (const operation of [
    api.createImportJob,
    api.detectSource,
    api.setSourceSync,
  ])
    expect(operation).not.toHaveBeenCalled();
}
beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
  vi.stubGlobal("crypto", webcrypto);
  delete window.oneMemory;
  sources = [];
  jobs = [];
  vi.mocked(api.getHealth).mockResolvedValue({
    status: "ok",
    service: "one-memory-core",
    version: "fixture",
    instance_id: "fixture-instance",
  });
  vi.mocked(api.getSources).mockImplementation(async () =>
    sources.map((item) => ({ ...item })),
  );
  vi.mocked(api.getImportJobs).mockImplementation(async () =>
    jobs.map((job) => ({ ...job })),
  );
  vi.mocked(api.completeOnboarding).mockResolvedValue({
    ...state,
    status: "completed",
  });
  vi.mocked(api.connectSources).mockImplementation(
    async (ids, _confirmed, options) => ({
      request_id: options?.requestId ?? null,
      accepted_source_ids: ids,
      failed_sources: {},
      jobs,
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
});

it("defaults to all three readable apps without authorization and preserves explicit deselection after refresh", async () => {
  sources = [source("workbuddy"), source("cursor"), source("codex")];
  await ready();
  await waitFor(() => expect(count()).toBe("3"));
  expect(screen.getByRole("list", { name: "候选应用" }).children).toHaveLength(
    3,
  );
  for (const item of sources)
    expect(checkbox(item.source_id).checked).toBe(true);
  expect(api.connectSources).not.toHaveBeenCalled();
  noUnrequestedWrites();
  fireEvent.click(checkbox("cursor"));
  expect(count()).toBe("2");
  fireEvent.click(screen.getByRole("button", { name: "重新检测位置" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "重新检测位置" })).toHaveProperty(
      "disabled",
      false,
    ),
  );
  expect(checkbox("cursor").checked).toBe(false);
  expect(count()).toBe("2");
  fireEvent.click(screen.getByRole("button", { name: "开始导入" }));
  await screen.findByRole("region", { name: "导入结果" });
  expect(api.connectSources).toHaveBeenCalledExactlyOnceWith(
    ["codex", "workbuddy"],
    true,
    expect.objectContaining({
      onboarding: true,
      fullRescan: true,
      sourceRoots: { codex: "root-codex", workbuddy: "root-workbuddy" },
    }),
  );
  noUnrequestedWrites();
});

it("canceling custom location does not select it on later metadata refresh", async () => {
  sources = [
    source("workbuddy"),
    source("codex", { candidate_kind: "environment_override" }),
  ];
  await ready();
  await waitFor(() => expect(count()).toBe("1"));
  fireEvent.click(checkbox("codex"));
  const dialog = screen.getByRole("dialog", { name: "确认自定义位置" });
  fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
  fireEvent.click(screen.getByRole("button", { name: "重新检测位置" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "重新检测位置" })).toHaveProperty(
      "disabled",
      false,
    ),
  );
  expect(checkbox("codex").checked).toBe(false);
  expect(count()).toBe("1");
  expect(api.connectSources).not.toHaveBeenCalled();
  noUnrequestedWrites();
});

it("keeps the single-source submit frozen when another source arrives during final refresh", async () => {
  sources = [source("workbuddy")];
  await ready();
  await waitFor(() => expect(count()).toBe("1"));
  let resolve!: (items: SourceResponse[]) => void;
  vi.mocked(api.getSources).mockReturnValueOnce(
    new Promise((done) => {
      resolve = done;
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "开始导入" }));
  expect(api.connectSources).not.toHaveBeenCalled();
  sources = [...sources, source("codex")];
  await act(async () => resolve(sources));
  await screen.findByRole("region", { name: "导入结果" });
  expect(api.connectSources).toHaveBeenCalledExactlyOnceWith(
    ["workbuddy"],
    true,
    expect.objectContaining({ sourceRoots: { workbuddy: "root-workbuddy" } }),
  );
  expect(screen.getByLabelText("已受理来源").textContent).toBe("workbuddy");
  noUnrequestedWrites();
});

it("empty discovery shows zero and allows explicit entry without a connection request", async () => {
  await ready();
  await waitFor(() => expect(count()).toBe("0"));
  expect(screen.getByRole("button", { name: "开始导入" })).toHaveProperty(
    "disabled",
    true,
  );
  fireEvent.click(screen.getByRole("button", { name: "稍后添加，先进入" }));
  await waitFor(() =>
    expect(finish).toHaveBeenCalledExactlyOnceWith("sources"),
  );
  expect(api.completeOnboarding).toHaveBeenCalledOnce();
  expect(api.connectSources).not.toHaveBeenCalled();
  noUnrequestedWrites();
});

it("partial acceptance preserves the successful job and failure through model-step back/continue without resubmission", async () => {
  sources = [source("workbuddy"), source("cursor")];
  const job = {
    job_id: "accepted-job",
    user_id: "local",
    source_id: "workbuddy",
    status: "completed",
    created_at: "2026-09-19T00:00:00Z",
    updated_at: "2026-09-19T00:00:01Z",
  } as ImportJob;
  vi.mocked(api.connectSources).mockImplementationOnce(
    async (_ids, _confirmed, options) => {
      sources = [
        source("workbuddy", { authorized: true, grant_revision: 1 }),
        source("cursor"),
      ];
      jobs = [job];
      return {
        request_id: options?.requestId ?? null,
        accepted_source_ids: ["workbuddy"],
        failed_sources: { cursor: "source_unavailable" },
        jobs: [job],
      };
    },
  );
  await ready();
  await waitFor(() => expect(count()).toBe("2"));
  fireEvent.click(screen.getByRole("button", { name: "开始导入" }));
  await screen.findByRole("region", { name: "导入结果" });
  expect(screen.getByLabelText("已受理来源").textContent).toBe("workbuddy");
  expect(screen.getByLabelText("失败来源").textContent).toBe("cursor");
  expect(screen.getByLabelText("保留任务").textContent).toBe(
    "accepted-job:completed",
  );
  fireEvent.click(screen.getByRole("button", { name: "继续配置模型" }));
  fireEvent.click(await screen.findByRole("button", { name: "返回导入" }));
  expect(screen.getByLabelText("保留任务").textContent).toBe(
    "accepted-job:completed",
  );
  expect(screen.getByLabelText("失败来源").textContent).toBe("cursor");
  expect(api.completeOnboarding).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "进入应用" }));
  await waitFor(() =>
    expect(finish).toHaveBeenCalledExactlyOnceWith("sources"),
  );
  expect(api.connectSources).toHaveBeenCalledOnce();
  noUnrequestedWrites();
});
