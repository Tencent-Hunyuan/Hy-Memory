import { describe, expect, it } from "vitest";
import {
  buildEmbeddingSwitchPlanRequest,
  buildEmbeddingSwitchStartRequest,
  EmbeddingSwitchInputError,
  embeddingSwitchCancelPath,
  parseEmbeddingSpaceDeleteReceipt,
  parseEmbeddingSpaces,
  parseEmbeddingSwitchCurrent,
  parseEmbeddingSwitchPlan,
  parseEmbeddingSwitchRefusal,
  retiredEmbeddingSpacePath,
} from "./embedding-switch-operation";

const NOW = "2026-11-03T09:00:00Z";

function summary(spaceId: string | null, model = "embed-a") {
  return {
    space_id: spaceId,
    model,
    dims: 512,
    collection_base: spaceId ? "memory" : "",
    vectors: spaceId ? 12 : null,
  };
}

function plan() {
  return {
    needed: true,
    reason: "embedding_identity_changed",
    plan_token: "plan-token",
    source: summary("space-1"),
    target: summary(null, "embed-b"),
    target_profile_id: "profile-2",
    target_revision: 4,
    target_provider_kind: "openai_compatible",
    target_local: false,
    sources: [
      {
        space_id: "space-1",
        collection_base: "memory",
        dims: 512,
        vectors: 12,
        unmerged: null,
      },
    ],
    total_vectors: 12,
    users: 2,
    graph_vectors: null,
    estimate_s: 3,
    requests: 1,
    disk_bytes_needed: 4096,
    disk_bytes_free: 8192,
    retention_days: 14,
    blockers: [],
  };
}

function job() {
  return {
    job_id: "job-1",
    kind: "switch",
    state: "reembedding",
    source: summary("space-1"),
    target: summary("space-2", "embed-b"),
    stray_space_ids: [],
    target_profile_id: "profile-2",
    target_revision: 4,
    requested_by: "app",
    progress: {
      phase: "vectors",
      done: 6,
      total: 12,
      percent: 50,
      embedded: 6,
      reused: 0,
      kept: 0,
      role: "memory",
      source_index: 1,
      sources: 1,
      rate_per_s: 2,
      eta_s: 3,
      updated_at: NOW,
    },
    cancel_requested: false,
    error_code: null,
    attempt: 1,
    created_at: NOW,
    updated_at: NOW,
    finished_at: null,
  };
}

function space() {
  return {
    space_id: "space-1",
    state: "retired",
    model: "embed-a",
    dims: 512,
    name_dims: 512,
    collection_base: "memory",
    naming: "legacy",
    graph_dir: "graph",
    provider_kind: "local_onnx",
    profile_id: null,
    profile_revision: null,
    vectors: 12,
    created_at: NOW,
    live_since: NOW,
    retired_at: NOW,
    keep_until: NOW,
    dropped_at: null,
  };
}

describe("embedding switch parsing", () => {
  it("parses the complete plan and preserves it on structured refusals", () => {
    expect(parseEmbeddingSwitchPlan(plan())).toEqual(plan());
    expect(
      parseEmbeddingSwitchRefusal({
        detail: "embedding_switch_plan_stale",
        plan: plan(),
      }),
    ).toEqual({ code: "embedding_switch_plan_stale", plan: plan() });
  });

  it("rejects malformed plan fields rather than defaulting them", () => {
    expect(
      parseEmbeddingSwitchPlan({ ...plan(), target_revision: 0 }),
    ).toBeNull();
    expect(
      parseEmbeddingSwitchPlan({ ...plan(), blockers: ["BAD CODE"] }),
    ).toBeNull();
    expect(
      parseEmbeddingSwitchRefusal({
        detail: "embedding_switch_plan_stale",
        plan: { ...plan(), sources: "not-an-array" },
      }),
    ).toBeNull();
  });

  it("parses current jobs and rejects unknown states or invalid progress", () => {
    const current = {
      schema_version: 1,
      phase: "running",
      writes_paused: true,
      scan_state: "done",
      scan_error: null,
      job: job(),
      last_finished: null,
    };
    expect(parseEmbeddingSwitchCurrent(current)).toEqual(current);
    expect(
      parseEmbeddingSwitchCurrent({
        ...current,
        job: { ...job(), state: "completed" },
      }),
    ).toBeNull();
    expect(
      parseEmbeddingSwitchCurrent({
        ...current,
        job: { ...job(), progress: { ...job().progress, percent: 101 } },
      }),
    ).toBeNull();
  });

  it("parses registry, scan and retired-space deletion receipts", () => {
    const spaces = {
      schema_version: 1,
      live_space_id: null,
      spaces: [space()],
      scanned: [
        {
          collection_base: "memory",
          name_dims: 512,
          width: 512,
          vectors: 12,
          counts: { memory: 12 },
          space_id: "space-1",
          state: "retired",
        },
      ],
      scan_error: null,
    };
    expect(parseEmbeddingSpaces(spaces)).toEqual(spaces);
    expect(
      parseEmbeddingSpaceDeleteReceipt({
        schema_version: 1,
        space: { ...space(), state: "dropped", dropped_at: NOW },
      }),
    ).not.toBeNull();
    expect(
      parseEmbeddingSpaces({
        ...spaces,
        scanned: [{ ...spaces.scanned[0], counts: { memory: -1 } }],
      }),
    ).toBeNull();
  });
});

describe("embedding switch requests", () => {
  it("builds bounded plan and start bodies", () => {
    expect(buildEmbeddingSwitchPlanRequest("profile/a", 4)).toEqual({
      path: "/api/v1/embedding-switches/plan",
      body: { profile_id: "profile/a", expected_revision: 4 },
    });
    expect(
      buildEmbeddingSwitchStartRequest({
        profile_id: "profile/a",
        expected_revision: 4,
        plan_token: "token",
        requested_by: "app",
      }),
    ).toEqual({
      path: "/api/v1/embedding-switches",
      body: {
        profile_id: "profile/a",
        expected_revision: 4,
        plan_token: "token",
        requested_by: "app",
      },
    });
  });

  it("encodes path ids and rejects invalid inputs before transport", () => {
    expect(embeddingSwitchCancelPath("job/a")).toContain("job%2Fa/cancel");
    expect(retiredEmbeddingSpacePath("space/a")).toContain("space%2Fa");
    expect(() => buildEmbeddingSwitchPlanRequest("", 1)).toThrow(
      EmbeddingSwitchInputError,
    );
    expect(() =>
      buildEmbeddingSwitchStartRequest({
        profile_id: "profile",
        expected_revision: 0,
        plan_token: "token",
        requested_by: "app",
      }),
    ).toThrow("expected_revision_invalid");
  });
});
