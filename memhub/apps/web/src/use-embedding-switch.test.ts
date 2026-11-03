// @vitest-environment jsdom
import type {
  EmbeddingSwitchCurrent,
  EmbeddingSwitchJob,
  EmbeddingSwitchPlan,
} from "@one-memory/contracts";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getCurrentEmbeddingSwitch: vi.fn(),
  planEmbeddingSwitch: vi.fn(),
  startEmbeddingSwitch: vi.fn(),
  cancelEmbeddingSwitch: vi.fn(),
  embeddingSwitchRefusal: vi.fn(
    (reason: { refusal?: unknown }) => reason?.refusal ?? null,
  ),
}));
vi.mock("./api", () => api);

import { pollNow, resetPollScheduler } from "./poll-scheduler";
import { useEmbeddingSwitchController as useEmbeddingSwitch } from "./use-embedding-switch";

const NOW = "2026-11-03T09:00:00Z";
const summary = (space_id: string | null) => ({
  space_id,
  model: "embed",
  dims: 512,
  collection_base: space_id ? "memory" : "",
  vectors: space_id ? 2 : null,
});
const plan: EmbeddingSwitchPlan = {
  needed: true,
  reason: "embedding_identity_changed",
  plan_token: "token",
  source: summary("space-1"),
  target: summary(null),
  target_profile_id: "profile-1",
  target_revision: 2,
  target_provider_kind: "openai_compatible",
  target_local: false,
  sources: [
    {
      space_id: "space-1",
      collection_base: "memory",
      dims: 512,
      vectors: 2,
      unmerged: null,
    },
  ],
  total_vectors: 2,
  users: 1,
  graph_vectors: null,
  estimate_s: 1,
  requests: 1,
  disk_bytes_needed: 2,
  disk_bytes_free: 4,
  retention_days: 14,
  blockers: [],
};
const job: EmbeddingSwitchJob = {
  job_id: "job-1",
  kind: "switch",
  state: "preparing",
  source: summary("space-1"),
  target: summary("space-2"),
  stray_space_ids: [],
  target_profile_id: "profile-1",
  target_revision: 2,
  requested_by: "app",
  progress: {
    phase: "prepare",
    done: 0,
    total: 2,
    percent: 0,
    embedded: 0,
    reused: 0,
    kept: 0,
    role: null,
    source_index: 0,
    sources: 1,
    rate_per_s: null,
    eta_s: null,
    updated_at: NOW,
  },
  cancel_requested: false,
  error_code: null,
  attempt: 1,
  created_at: NOW,
  updated_at: NOW,
  finished_at: null,
};
const idle: EmbeddingSwitchCurrent = {
  schema_version: 1,
  phase: "idle",
  writes_paused: false,
  scan_state: "done",
  scan_error: null,
  job: null,
  last_finished: null,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

beforeEach(() => {
  vi.resetAllMocks();
  resetPollScheduler();
  api.embeddingSwitchRefusal.mockImplementation(
    (reason: { refusal?: unknown }) => reason?.refusal ?? null,
  );
  api.getCurrentEmbeddingSwitch.mockResolvedValue(idle);
});
afterEach(() => {
  cleanup();
  resetPollScheduler();
});

it("loads current state and accepts shared polling updates", async () => {
  const { result } = renderHook(useEmbeddingSwitch);
  await waitFor(() => expect(result.current.current).toEqual(idle));
  const running = {
    ...idle,
    phase: "running",
    writes_paused: true,
    job,
  } as const;
  api.getCurrentEmbeddingSwitch.mockResolvedValue(running);
  await act(async () => {
    await pollNow("embedding-switch");
  });
  expect(result.current.current).toEqual(running);
  expect(result.current.refreshFailed).toBe(false);
});

it("plans and starts a switch without activating a profile", async () => {
  api.planEmbeddingSwitch.mockResolvedValue(plan);
  api.startEmbeddingSwitch.mockResolvedValue(job);
  const { result } = renderHook(useEmbeddingSwitch);
  await waitFor(() => expect(result.current.current).toEqual(idle));
  await act(async () => {
    expect(await result.current.planActivation("profile-1", 2)).toEqual(plan);
  });
  expect(result.current.plan).toEqual(plan);
  await act(async () => {
    expect(await result.current.startPlannedSwitch()).toEqual(job);
  });
  expect(api.startEmbeddingSwitch).toHaveBeenCalledWith({
    profile_id: "profile-1",
    expected_revision: 2,
    plan_token: "token",
    requested_by: "app",
  });
  expect(result.current.plan).toBeNull();
  expect(result.current.current?.job).toEqual(job);
});

it("does not let a pre-start poll overwrite the authoritative start receipt", async () => {
  api.planEmbeddingSwitch.mockResolvedValue(plan);
  api.startEmbeddingSwitch.mockResolvedValue(job);
  const { result } = renderHook(useEmbeddingSwitch);
  await waitFor(() => expect(result.current.current).toEqual(idle));
  await act(async () => {
    await result.current.planActivation("profile-1", 2);
  });
  const stale = deferred<EmbeddingSwitchCurrent>();
  api.getCurrentEmbeddingSwitch.mockReturnValueOnce(stale.promise);
  let polling!: Promise<void>;
  act(() => {
    polling = pollNow("embedding-switch");
  });
  await act(async () => {
    await result.current.startPlannedSwitch();
  });
  expect(result.current.current?.job).toEqual(job);
  await act(async () => {
    stale.resolve(idle);
    await polling;
  });
  expect(result.current.current?.job).toEqual(job);
});

it("does not let a poll started during the mutation overwrite its receipt", async () => {
  api.planEmbeddingSwitch.mockResolvedValue(plan);
  const starting = deferred<EmbeddingSwitchJob>();
  api.startEmbeddingSwitch.mockReturnValue(starting.promise);
  const { result } = renderHook(useEmbeddingSwitch);
  await waitFor(() => expect(result.current.current).toEqual(idle));
  await act(async () => {
    await result.current.planActivation("profile-1", 2);
  });
  let start!: Promise<EmbeddingSwitchJob | undefined>;
  act(() => {
    start = result.current.startPlannedSwitch();
  });
  const stale = deferred<EmbeddingSwitchCurrent>();
  api.getCurrentEmbeddingSwitch.mockReturnValueOnce(stale.promise);
  let polling!: Promise<void>;
  act(() => {
    polling = pollNow("embedding-switch");
  });
  await act(async () => {
    starting.resolve(job);
    await start;
  });
  await act(async () => {
    stale.resolve(idle);
    await polling;
  });
  expect(result.current.current?.job).toEqual(job);
});

it("falls back cleanly when an older Core has no switch routes", async () => {
  const missing = {
    response: { status: 404, data: { detail: "Not Found" } },
  };
  api.getCurrentEmbeddingSwitch.mockRejectedValue(missing);
  const { result } = renderHook(useEmbeddingSwitch);
  await waitFor(() => expect(result.current.supported).toBe(false));
  expect(result.current.shouldUseLegacyActivation()).toBe(true);
  const reads = api.getCurrentEmbeddingSwitch.mock.calls.length;
  await act(async () => {
    await pollNow("embedding-switch");
  });
  expect(api.getCurrentEmbeddingSwitch).toHaveBeenCalledTimes(reads);
});

it("does not treat a structured product 404 as an older Core", async () => {
  api.getCurrentEmbeddingSwitch.mockRejectedValue({
    response: {
      status: 404,
      data: { detail: "model_profile_not_found" },
    },
  });
  const { result } = renderHook(useEmbeddingSwitch);
  await waitFor(() => expect(result.current.refreshFailed).toBe(true));
  expect(result.current.supported).not.toBe(false);
  expect(result.current.shouldUseLegacyActivation()).toBe(false);
});

it("adopts the replacement plan from a stale-plan refusal", async () => {
  const replacement = { ...plan, plan_token: "new-token", total_vectors: 3 };
  api.planEmbeddingSwitch.mockRejectedValue({
    refusal: { code: "embedding_switch_plan_stale", plan: replacement },
  });
  const { result } = renderHook(useEmbeddingSwitch);
  await waitFor(() => expect(result.current.current).toEqual(idle));
  await act(async () => {
    await result.current.planActivation("profile-1", 2);
  });
  expect(result.current.plan).toEqual(replacement);
  expect(result.current.errorCode).toBe("embedding_switch_plan_stale");
});

it("cancels only the current job and merges the receipt", async () => {
  api.getCurrentEmbeddingSwitch.mockResolvedValue({
    ...idle,
    phase: "running",
    writes_paused: true,
    job,
  });
  api.cancelEmbeddingSwitch.mockResolvedValue({
    ...job,
    cancel_requested: true,
  });
  const { result } = renderHook(useEmbeddingSwitch);
  await waitFor(() => expect(result.current.current?.job).toEqual(job));
  await act(async () => {
    await result.current.cancelCurrentSwitch();
  });
  expect(api.cancelEmbeddingSwitch).toHaveBeenCalledWith("job-1");
  expect(result.current.current?.job?.cancel_requested).toBe(true);
});

it("does not expose arbitrary transport error messages", async () => {
  api.planEmbeddingSwitch.mockRejectedValue(
    new Error("request failed with secret=do-not-display"),
  );
  const { result } = renderHook(useEmbeddingSwitch);
  await waitFor(() => expect(result.current.current).toEqual(idle));
  await act(async () => {
    await result.current.planActivation("profile-1", 2);
  });
  expect(result.current.errorCode).toBe("embedding_switch_failed");
});
