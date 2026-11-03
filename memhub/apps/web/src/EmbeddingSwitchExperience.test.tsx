// @vitest-environment jsdom

import type {
  EmbeddingSwitchCurrent,
  EmbeddingSwitchJob,
  EmbeddingSwitchPlan,
} from "@one-memory/contracts";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

const cancelCurrentSwitch = vi.fn();
let current: EmbeddingSwitchCurrent | null = null;

vi.mock("./use-embedding-switch", () => ({
  useEmbeddingSwitch: () => ({
    current,
    busy: null,
    cancelCurrentSwitch,
  }),
}));
vi.mock("./api", () => ({
  deleteRetiredEmbeddingSpace: vi.fn(),
}));

import {
  EmbeddingSwitchConfirmDialog,
  EmbeddingSwitchGlobalStatus,
} from "./EmbeddingSwitchExperience";
import { LANGUAGE_STORAGE_KEY, LanguageProvider } from "./gui-language";

const NOW = "2026-11-03T09:00:00Z";
const space = (id: string | null, model: string) => ({
  space_id: id,
  model,
  dims: model === "old-search" ? 512 : 1024,
  collection_base: id ? "memory" : "",
  vectors: id ? 8421 : null,
});
const plan: EmbeddingSwitchPlan = {
  needed: true,
  reason: "embedding_identity_changed",
  plan_token: "token",
  source: space("space-old", "old-search"),
  target: space(null, "new-search"),
  target_profile_id: "profile-1",
  target_revision: 2,
  target_provider_kind: "openai_compatible",
  target_local: false,
  sources: [
    {
      space_id: "space-old",
      collection_base: "memory",
      dims: 512,
      vectors: 8421,
      unmerged: null,
    },
  ],
  total_vectors: 8421,
  users: 1,
  graph_vectors: null,
  estimate_s: 120,
  requests: 43,
  disk_bytes_needed: 72 * 1024 * 1024,
  disk_bytes_free: 1024 * 1024 * 1024,
  retention_days: 14,
  blockers: [],
};
const job: EmbeddingSwitchJob = {
  job_id: "job-1",
  kind: "switch",
  state: "reembedding",
  source: space("space-old", "old-search"),
  target: space("space-new", "new-search"),
  stray_space_ids: [],
  target_profile_id: "profile-1",
  target_revision: 2,
  requested_by: "app",
  progress: {
    phase: "vectors",
    done: 4884,
    total: 8421,
    percent: 58,
    embedded: 4884,
    reused: 0,
    kept: 0,
    role: "memory",
    source_index: 0,
    sources: 1,
    rate_per_s: 90,
    eta_s: 52,
    updated_at: NOW,
  },
  cancel_requested: false,
  error_code: null,
  attempt: 1,
  created_at: NOW,
  updated_at: NOW,
  finished_at: null,
};

afterEach(() => {
  cleanup();
  current = null;
  localStorage.removeItem(LANGUAGE_STORAGE_KEY);
  vi.clearAllMocks();
});

it("switches Chinese, Spanish, and English without leaking Chinese", async () => {
  localStorage.setItem(LANGUAGE_STORAGE_KEY, "zh-Hans");
  const view = render(
    <LanguageProvider>
      <EmbeddingSwitchConfirmDialog
        plan={{ ...plan, users: 2 }}
        busy={false}
        errorCode={null}
        onCancel={vi.fn()}
        onStart={vi.fn()}
      />
    </LanguageProvider>,
  );

  await waitFor(() =>
    expect(
      screen.getByRole("heading", { name: "更新记忆搜索索引？" }),
    ).toBeTruthy(),
  );

  localStorage.setItem(LANGUAGE_STORAGE_KEY, "es");
  window.dispatchEvent(
    new StorageEvent("storage", { key: LANGUAGE_STORAGE_KEY }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("heading", {
        name: "¿Actualizar el índice de búsqueda de memorias?",
      }),
    ).toBeTruthy(),
  );
  expect(view.container.textContent).not.toMatch(/[一-鿿]/);
  expect(view.container.textContent).toContain("8421");

  localStorage.setItem(LANGUAGE_STORAGE_KEY, "en");
  window.dispatchEvent(
    new StorageEvent("storage", { key: LANGUAGE_STORAGE_KEY }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("heading", {
        name: "Update the memory search index?",
      }),
    ).toBeTruthy(),
  );
  expect(view.container.textContent).not.toMatch(/[一-鿿]/);
  expect(view.container.textContent).toContain("8,421");
});

it("explains the index update in user terms before exposing technical details", async () => {
  const onStart = vi.fn();
  render(
    <EmbeddingSwitchConfirmDialog
      plan={plan}
      busy={false}
      errorCode={null}
      onCancel={vi.fn()}
      onStart={onStart}
    />,
  );

  expect(
    screen.getByRole("heading", { name: "更新记忆搜索索引？" }),
  ).toBeTruthy();
  expect(screen.getByText("8,421 条")).toBeTruthy();
  expect(screen.getByText("约 2 分钟")).toBeTruthy();
  expect(screen.getByText(/失败或取消时仍使用原来的模型/)).toBeTruthy();
  await userEvent.click(screen.getByText("查看技术详情"));
  expect(screen.getByText("512 → 1024")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "开始更新索引" }));
  expect(onStart).toHaveBeenCalledTimes(1);
});

it("blocks start when Core reports a prerequisite", () => {
  render(
    <EmbeddingSwitchConfirmDialog
      plan={{ ...plan, blockers: ["insufficient_disk_space"] }}
      busy={false}
      errorCode={null}
      onCancel={vi.fn()}
      onStart={vi.fn()}
    />,
  );
  expect(screen.getByText(/剩余空间不足/)).toBeTruthy();
  expect(screen.getByRole("button", { name: "开始更新索引" })).toHaveProperty(
    "disabled",
    true,
  );
});

it("keeps a running switch visible and cancellable outside settings", async () => {
  current = {
    schema_version: 1,
    phase: "running",
    writes_paused: true,
    scan_state: "done",
    scan_error: null,
    job,
    last_finished: null,
  };
  render(
    <EmbeddingSwitchGlobalStatus
      onConfigureSearch={vi.fn()}
      onDiagnostics={vi.fn()}
    />,
  );

  expect(screen.getByText("正在更新记忆索引")).toBeTruthy();
  expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe(
    "58",
  );
  await userEvent.click(screen.getByRole("button", { name: "取消更新" }));
  expect(cancelCurrentSwitch).toHaveBeenCalledTimes(1);
});

it("shows startup index inspection without blocking the rest of the app", () => {
  current = {
    schema_version: 1,
    phase: "scanning",
    writes_paused: true,
    scan_state: "scanning",
    scan_error: null,
    job: null,
    last_finished: null,
  };
  render(
    <EmbeddingSwitchGlobalStatus
      onConfigureSearch={vi.fn()}
      onDiagnostics={vi.fn()}
    />,
  );

  expect(screen.getByText("正在检查本机的记忆搜索索引")).toBeTruthy();
  expect(screen.getByText(/搜索仍可使用/)).toBeTruthy();
});

it("explains rollback safety and gives recovery actions", async () => {
  current = {
    schema_version: 1,
    phase: "running",
    writes_paused: true,
    scan_state: "done",
    scan_error: null,
    job,
    last_finished: null,
  };
  const onConfigureSearch = vi.fn();
  const onDiagnostics = vi.fn();
  const view = render(
    <EmbeddingSwitchGlobalStatus
      onConfigureSearch={onConfigureSearch}
      onDiagnostics={onDiagnostics}
    />,
  );

  current = {
    schema_version: 1,
    phase: "idle",
    writes_paused: false,
    scan_state: "done",
    scan_error: null,
    job: null,
    last_finished: {
      ...job,
      state: "rolled_back",
      error_code: "embedding_service_failed",
      progress: { ...job.progress, phase: "done" },
      finished_at: NOW,
    },
  };
  view.rerender(
    <EmbeddingSwitchGlobalStatus
      onConfigureSearch={onConfigureSearch}
      onDiagnostics={onDiagnostics}
    />,
  );

  await waitFor(() => expect(screen.getByText(/记忆没有丢失/)).toBeTruthy());
  await userEvent.click(screen.getByRole("button", { name: "检查连接" }));
  expect(onConfigureSearch).toHaveBeenCalledTimes(1);
});
