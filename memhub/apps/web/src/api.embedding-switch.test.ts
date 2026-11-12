// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

const fake = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  del: vi.fn(),
}));
vi.mock("axios", () => ({
  default: {
    create: () => ({
      get: fake.get,
      post: fake.post,
      delete: fake.del,
      interceptors: { request: { use: vi.fn() } },
    }),
    isAxiosError: (value: unknown) =>
      typeof value === "object" && value !== null && "isAxiosError" in value,
  },
}));

import {
  cancelEmbeddingSwitch,
  deleteRetiredEmbeddingSpace,
  embeddingSwitchRefusal,
  embeddingSwitchUnsupported,
  getCurrentEmbeddingSwitch,
  listEmbeddingSpaces,
  planEmbeddingSwitch,
  startEmbeddingSwitch,
} from "./api";

const NOW = "2026-11-03T09:00:00Z";
const summary = (space_id: string | null) => ({
  space_id,
  model: "embed",
  dims: 512,
  collection_base: space_id ? "memory" : "",
  vectors: space_id ? 1 : null,
});
const plan = {
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
      vectors: 1,
      unmerged: null,
    },
  ],
  total_vectors: 1,
  users: 1,
  graph_vectors: null,
  estimate_s: 1,
  requests: 1,
  disk_bytes_needed: 1,
  disk_bytes_free: 2,
  retention_days: 14,
  blockers: [],
} as const;
const job = {
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
    total: 1,
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
} as const;
const space = {
  space_id: "space-1",
  state: "retired",
  model: "embed",
  dims: 512,
  name_dims: 512,
  collection_base: "memory",
  naming: "legacy",
  graph_dir: "graph",
  provider_kind: "local_onnx",
  profile_id: null,
  profile_revision: null,
  vectors: 1,
  created_at: NOW,
  live_since: NOW,
  retired_at: NOW,
  keep_until: NOW,
  dropped_at: null,
} as const;

beforeEach(() => vi.resetAllMocks());

describe("embedding switch API", () => {
  it("plans and starts through validated request bodies", async () => {
    fake.post
      .mockResolvedValueOnce({ data: plan })
      .mockResolvedValueOnce({ data: job });
    await expect(planEmbeddingSwitch("profile-1", 2)).resolves.toEqual(plan);
    await expect(
      startEmbeddingSwitch({
        profile_id: "profile-1",
        expected_revision: 2,
        plan_token: "token",
        requested_by: "app",
      }),
    ).resolves.toEqual(job);
    expect(fake.post.mock.calls[0].slice(0, 2)).toEqual([
      "/api/v1/embedding-switches/plan",
      { profile_id: "profile-1", expected_revision: 2 },
    ]);
    expect(fake.post.mock.calls[1][0]).toBe("/api/v1/embedding-switches");
  });

  it("reads, cancels, lists and deletes with parsed receipts", async () => {
    const current = {
      schema_version: 1,
      phase: "running",
      writes_paused: true,
      scan_state: "done",
      scan_error: null,
      job,
      last_finished: null,
    };
    const spaces = {
      schema_version: 1,
      live_space_id: null,
      spaces: [space],
      scanned: null,
      scan_error: null,
    };
    fake.get
      .mockResolvedValueOnce({ data: current })
      .mockResolvedValueOnce({ data: spaces });
    fake.post.mockResolvedValueOnce({
      data: { ...job, cancel_requested: true },
    });
    fake.del.mockResolvedValueOnce({
      data: {
        schema_version: 1,
        space: { ...space, state: "dropped", dropped_at: NOW },
      },
    });
    await expect(getCurrentEmbeddingSwitch()).resolves.toEqual(current);
    await expect(cancelEmbeddingSwitch("job-1")).resolves.toMatchObject({
      cancel_requested: true,
    });
    await expect(listEmbeddingSpaces()).resolves.toEqual(spaces);
    await expect(deleteRetiredEmbeddingSpace("space-1")).resolves.toMatchObject(
      { space: { state: "dropped" } },
    );
    expect(fake.post.mock.calls[0][0]).toBe(
      "/api/v1/embedding-switches/job-1/cancel",
    );
    expect(fake.del.mock.calls[0][0]).toBe("/api/v1/embedding-spaces/space-1");
  });

  it("keeps a replacement plan attached to a refusal", async () => {
    const refusal = Object.assign(new Error("conflict"), {
      isAxiosError: true,
      response: {
        status: 409,
        data: { detail: "embedding_switch_plan_stale", plan },
      },
    });
    fake.post.mockRejectedValueOnce(refusal);
    let caught: unknown;
    try {
      await planEmbeddingSwitch("profile-1", 2);
    } catch (reason) {
      caught = reason;
    }
    expect(embeddingSwitchRefusal(caught)).toEqual({
      code: "embedding_switch_plan_stale",
      plan,
    });
  });

  it("rejects malformed success bodies", async () => {
    fake.get.mockResolvedValueOnce({
      data: {
        schema_version: 1,
        phase: "running",
        writes_paused: true,
        scan_state: "done",
        scan_error: null,
        job: { ...job, state: "surprise" },
        last_finished: null,
      },
    });
    await expect(getCurrentEmbeddingSwitch()).rejects.toThrow(
      "embedding_switch_current_invalid",
    );
  });

  it("distinguishes an older Core route 404 from a product refusal", () => {
    const routeMissing = {
      isAxiosError: true,
      response: { status: 404, data: { detail: "Not Found" } },
    };
    const productMissing = {
      isAxiosError: true,
      response: {
        status: 404,
        data: { detail: "embedding_switch_not_found" },
      },
    };

    expect(embeddingSwitchUnsupported(routeMissing)).toBe(true);
    expect(embeddingSwitchUnsupported(productMissing)).toBe(false);
  });
});
