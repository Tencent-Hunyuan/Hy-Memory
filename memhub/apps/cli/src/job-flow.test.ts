import { describe, expect, it, vi } from "vitest";

const transport = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(async () => ({ status: 200, data: {} })),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
  request: vi.fn(),
}));

// The write allow-list test constructs a real operations client against the
// daily port. Without this mock it sends real POSTs to whatever Core is
// listening on 8765 (D25). The client never inspects the response beyond its
// status for these paths, so a 2xx stub is enough.
vi.mock("axios", () => ({
  default: { create: vi.fn(() => transport), get: vi.fn() },
}));

import type { CoreOperations } from "./core-operations.js";
import { createCoreOperations } from "./core-operations.js";
import {
  executeJobAction,
  executeJobStart,
  jobExitCode,
  planJobAction,
  planJobStart,
} from "./job-flow.js";

function operations(
  post: (path: string, body: Record<string, unknown>) => Promise<unknown>,
): CoreOperations {
  return {
    post: post as unknown as CoreOperations["post"],
  };
}

const JOB = {
  job_id: "job-1",
  user_id: "local",
  source_id: "codex",
  operation: "history" as const,
  status: "paused" as const,
  phase: null,
  checkpoint: null,
  discovered: 1,
  discovered_targets: 1,
  emitted: 1,
  stored: 1,
  revised: 0,
  merged: 0,
  skipped: 0,
  errors: 0,
  created_at: "2026-09-30T00:00:00Z",
  updated_at: "2026-09-30T00:01:00Z",
};

describe("jobs command flow", () => {
  it("plans start without mutation and requires exact source/operation", () => {
    expect(planJobStart("codex", "history")).toMatchObject({
      state: "preview",
      plan: {
        kind: "import_job_start",
        source_id: "codex",
        operation: "history",
      },
    });
    expect(planJobStart("../codex", "history")).toMatchObject({
      state: "invalid",
      code: "invalid_source_id",
    });
  });

  it("plans pause/resume/cancel without mutation", () => {
    for (const action of ["pause", "resume", "cancel"] as const) {
      expect(planJobAction("job-1", action)).toMatchObject({
        state: "preview",
        plan: { kind: "import_job_action", job_id: "job-1", action },
      });
    }
  });

  it("posts the exact start body and validates the returned job", async () => {
    const post = vi.fn(async () => ({
      state: "committed" as const,
      data: JOB,
    }));
    const document = await executeJobStart(
      { kind: "import_job_start", source_id: "codex", operation: "sync" },
      operations(post),
    );
    expect(post).toHaveBeenCalledWith("/api/v1/import-jobs", {
      source_id: "codex",
      operation: "sync",
    });
    expect(document).toMatchObject({
      state: "committed",
      operation_id: "codex",
      job: { job_id: "job-1" },
    });
    expect(jobExitCode(document)).toBe(0);
  });

  it.each(["pause", "resume", "cancel"] as const)(
    "posts the exact %s action path",
    async (action) => {
      const post = vi.fn(async () => ({
        state: "committed" as const,
        data: { ...JOB, status: action === "resume" ? "running" : "paused" },
      }));
      const document = await executeJobAction(
        { kind: "import_job_action", job_id: "job-1", action },
        operations(post),
      );
      expect(post).toHaveBeenCalledWith(
        `/api/v1/import-jobs/job-1/${action}`,
        {},
      );
      expect(document.state).toBe("committed");
    },
  );

  it("fails closed on a malformed 2xx receipt", async () => {
    const document = await executeJobAction(
      { kind: "import_job_action", job_id: "job-1", action: "pause" },
      operations(async () => ({
        state: "committed",
        data: { status: "paused" },
      })),
    );
    expect(document).toMatchObject({
      state: "invalid",
      code: "job_receipt_invalid",
    });
    expect(jobExitCode(document)).toBe(2);
  });

  it("maps rejected and unknown outcomes to non-zero", async () => {
    const rejected = await executeJobAction(
      { kind: "import_job_action", job_id: "job-1", action: "cancel" },
      operations(async () => ({
        state: "rejected",
        code: "invalid_job_transition",
        http_status: 409,
      })),
    );
    expect(rejected).toMatchObject({
      state: "rejected",
      code: "invalid_job_transition",
    });
    expect(jobExitCode(rejected)).toBe(1);

    const unknown = await executeJobStart(
      { kind: "import_job_start", source_id: "codex", operation: "history" },
      operations(async () => ({
        state: "unknown",
        code: "transport_result_unknown",
      })),
    );
    expect(jobExitCode(unknown)).toBe(1);
  });
});

describe("import-job write allow-list", () => {
  function attachment() {
    return {
      host: "127.0.0.1",
      port: 8765,
      apiToken: "token",
    };
  }

  it("allows the three exact action paths and rejects lookalikes", async () => {
    const operations = createCoreOperations(attachment());
    for (const action of ["pause", "resume", "cancel"]) {
      await expect(
        operations.post(`/api/v1/import-jobs/job-1/${action}`, {}),
      ).resolves.toBeDefined();
    }
    await expect(
      operations.post("/api/v1/import-jobs/job-1/delete", {}),
    ).rejects.toThrow("core_operation_path_not_allowed");
    await expect(
      operations.post("/api/v1/model-content-permission", { allowed: true }),
    ).rejects.toThrow("core_operation_path_not_allowed");
  });
});
