import type { EvolveOverview, EvolveRunReceipt } from "@one-memory/contracts";
import { afterEach, expect, it, vi } from "vitest";
import { getEvolveOverview } from "./api";
import {
  commitEvolveRun,
  localEvolveRunning,
  refreshEvolveOverview,
} from "./evolve-state";
import { queryClient, resetClient } from "./query-client";

vi.mock("./api", () => ({ getEvolveOverview: vi.fn() }));

const receipt: EvolveRunReceipt = {
  run_id: "run-1",
  user_id: "local",
  agent_id: "all-apps",
  max_batches: null,
  trigger: "manual",
  state: "running",
  started_at: "2026-11-05T14:00:00Z",
  finished_at: null,
  rounds: 0,
  tasks_processed: 0,
  batch_failures: 0,
  cycle_status: null,
  should_continue: null,
  continuation_reason: null,
  remaining_clusters: null,
  remaining_actionable_clusters: null,
  remaining_fresh_facts: null,
  capped: false,
  elapsed_ms: 0,
  error_code: null,
  failure_kind: null,
  request_id: "request-1",
};

const overview: EvolveOverview = {
  mode: "ultra",
  schedule: { enabled: true, time: "03:00" },
  users: {},
  running: [],
};

afterEach(() => {
  resetClient();
  vi.clearAllMocks();
});

it("shares the latest server overview through the query cache", async () => {
  vi.mocked(getEvolveOverview).mockResolvedValue(overview);

  await expect(refreshEvolveOverview()).resolves.toEqual(overview);
  expect(queryClient.getQueryData(["evolve", "local"])).toEqual(overview);
});

it("publishes an accepted local run immediately from a cold cache", () => {
  commitEvolveRun(receipt, overview);

  const current = queryClient.getQueryData<EvolveOverview>(["evolve", "local"]);
  expect(current?.running).toEqual([receipt]);
  expect(localEvolveRunning(current)).toBe(true);
});

it("does not let a pre-mutation overview overwrite the accepted run", async () => {
  let resolveRead: ((value: EvolveOverview) => void) | undefined;
  vi.mocked(getEvolveOverview).mockReturnValue(
    new Promise((resolve) => {
      resolveRead = resolve;
    }),
  );
  const staleRead = refreshEvolveOverview();

  commitEvolveRun(receipt, overview);
  resolveRead?.(overview);

  await expect(staleRead).resolves.toMatchObject({ running: [receipt] });
  expect(
    queryClient.getQueryData<EvolveOverview>(["evolve", "local"])?.running,
  ).toEqual([receipt]);
});
