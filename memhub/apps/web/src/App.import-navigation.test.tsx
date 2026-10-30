// @vitest-environment jsdom
import type { ImportJob, SourceResponse } from "@one-memory/contracts";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { App } from "./App";
import { SourceJobActions } from "./SourceJobActions";

const { imports, getOnboarding } = vi.hoisted(() => {
  const source: SourceResponse = {
    source_id: "cursor",
    display_name: "Cursor",
    storage_kinds: ["jsonl"],
    available: true,
    authorized: true,
    detected: true,
    root_label: "Synthetic source",
    candidate_path: "/synthetic/cursor",
    candidate_kind: "default",
    imported_messages: 1,
    imported_conversations: 1,
    capabilities: {
      historical_import: "implemented",
      incremental_import: "implemented",
      live_sync: "implemented",
      recall: "not_implemented",
      installer: "not_implemented",
      verification: "fixture_only",
    },
    sync_enabled: true,
    sync_completeness: "additions_only",
  };
  const job: ImportJob = {
    job_id: "synthetic-job",
    user_id: "local",
    source_id: source.source_id,
    operation: "history",
    status: "running",
    phase: "emit",
    checkpoint: null,
    discovered: 2,
    discovered_targets: 1,
    emitted: 1,
    stored: 1,
    revised: 0,
    merged: 0,
    skipped: 0,
    errors: 0,
    created_at: "2026-09-20T00:00:00Z",
    updated_at: "2026-09-20T00:00:01Z",
  };
  return {
    getOnboarding: vi.fn(),
    imports: {
      sources: [source],
      jobs: [job],
      loading: false,
      health: null,
      healthStale: false,
      sourceSnapshot: { stale: false, confirmedAt: job.updated_at },
      refresh: vi.fn(),
      changeJob: vi.fn(),
      setSync: vi.fn(),
      syncAll: vi.fn(),
      startImport: vi.fn(),
      connectSelected: vi.fn(),
      connectForOnboarding: vi.fn(),
      authorize: vi.fn(),
      revoke: vi.fn(),
      detect: vi.fn(),
    },
  };
});

vi.mock("./api", () => ({
  getOnboarding,
  getSources: vi.fn().mockResolvedValue([]),
}));
vi.mock("./use-import-sources", () => ({ useImportSources: () => imports }));
vi.mock("./ApplicationsHome", () => ({
  ApplicationsHome: ({ onManage }: { onManage(sourceId?: string): void }) => (
    <section aria-label="Applications home">
      <button type="button" onClick={() => onManage("cursor")}>
        Open Cursor detail
      </button>
      <button type="button" onClick={() => onManage()}>
        Manage sources
      </button>
    </section>
  ),
}));
vi.mock("./ApplicationDetail", () => ({
  ApplicationDetail: () => <section aria-label="Cursor detail" />,
}));
// Keep the real job controls and strip; isolate navigation from source discovery.
vi.mock("./SourcesView", () => ({
  SourcesView: ({ imports: state }: { imports: typeof imports }) => (
    <section aria-label="Source management">
      <SourceJobActions
        job={state.jobs[0]}
        sourceLabel={state.sources[0].display_name}
        syncEnabled={state.sources[0].sync_enabled === true}
        onAction={state.changeJob}
      />
    </section>
  ),
}));
vi.mock("./R6Settings", () => ({
  R6Settings: ({ onManageApps }: { onManageApps(): void }) => (
    <button type="button" onClick={onManageApps}>
      Manage apps from settings
    </button>
  ),
}));
vi.mock("./MemorySpace", () => ({ MemorySpace: () => null }));
vi.mock("./HealthPanel", () => ({ HealthPanel: () => null }));
vi.mock("./OnboardingFlow", () => ({ OnboardingFlow: () => null }));

beforeEach(() => {
  vi.clearAllMocks();
  getOnboarding.mockResolvedValue({ status: "completed" });
  imports.changeJob.mockResolvedValue({
    ...imports.jobs[0],
    status: "paused",
    updated_at: "2026-09-20T00:00:02Z",
  });
  window.history.replaceState({}, "", "/");
  window.oneMemory = undefined;
  localStorage.clear();
});
afterEach(cleanup);

async function rail(label: string) {
  const navigation = screen.getByRole("navigation", { name: "主要导航" });
  await userEvent.click(
    within(navigation).getByRole("button", {
      name: label === "应用" ? /^应用\d*$/ : label,
    }),
  );
}

function noSourceActions() {
  for (const action of [
    imports.changeJob,
    imports.setSync,
    imports.syncAll,
    imports.startImport,
    imports.connectSelected,
    imports.connectForOnboarding,
    imports.authorize,
    imports.revoke,
    imports.detect,
  ])
    expect(action).not.toHaveBeenCalled();
}

async function startFrom(destination: "home" | "detail") {
  render(<App />);
  await screen.findByRole("region", { name: "Applications home" });
  if (destination === "detail") {
    await userEvent.click(
      screen.getByRole("button", { name: "Open Cursor detail" }),
    );
    expect(screen.getByRole("region", { name: "Cursor detail" })).toBeTruthy();
  }
  // The global strip is shown in settings while the sources subview is retained.
  await rail("设置");
}

it.each(["home", "detail"] as const)(
  "keeps the settings strip without a source link and restores retained %s state",
  async (destination) => {
    await startFrom(destination);
    expect(screen.getByRole("status").textContent).toContain("正在");
    expect(screen.queryByRole("button", { name: "查看来源" })).toBeNull();
    expect(screen.queryByText("查看进度与详情")).toBeNull();
    await rail("应用");
    if (destination === "detail") {
      expect(
        screen.getByRole("region", { name: "Cursor detail" }),
      ).toBeTruthy();
    } else {
      expect(
        screen.getByRole("region", { name: "Applications home" }),
      ).toBeTruthy();
    }
    noSourceActions();
  },
);

it("keeps ordinary apps navigation on home without opening management", async () => {
  await startFrom("home");
  await rail("应用");
  expect(
    screen.getByRole("region", { name: "Applications home" }),
  ).toBeTruthy();
  expect(
    screen.queryByRole("region", { name: "Source management" }),
  ).toBeNull();
  noSourceActions();
});
