import { describe, expect, it, vi } from "vitest";
import {
  batchScanExitCode,
  executeBatchImport,
  planBatchImport,
  renderBatchScanText,
} from "./batch-import-flow.js";
import type { CoreReader, ReadOutcome } from "./core-client.js";
import type { CoreOperations } from "./core-operations.js";

function readerFor(catalog: unknown): CoreReader {
  return (async (path: string) =>
    path === "/api/v1/sources"
      ? ({ state: "ok", data: catalog } as ReadOutcome<unknown>)
      : ({
          state: "not_found",
          code: "http_404",
        } as ReadOutcome<unknown>)) as CoreReader;
}

function found(source_id: string, extra: Record<string, unknown> = {}) {
  return {
    source_id,
    display_name: source_id,
    storage_kinds: ["history"],
    available: true,
    authorized: false,
    detected: true,
    root_label: source_id,
    imported_messages: 0,
    imported_conversations: 0,
    capabilities: {
      historical_import: "implemented",
      incremental_import: "implemented",
      live_sync: "implemented",
      recall: "implemented",
      installer: "implemented",
      verification: "verified",
    },
    sync_completeness: "additions_only",
    location_state: "found",
    candidate_kind: "default",
    candidate_root_id: `root:v2:${"0".repeat(64)}`,
    candidate_revision: 0,
    requires_override_confirmation: false,
    ...extra,
  };
}

function missing(source_id: string) {
  return {
    ...found(source_id),
    available: false,
    detected: false,
    location_state: "not_found",
    candidate_root_id: null,
  };
}

describe("A19 batch planning", () => {
  it("keeps only found sources and lists not_found ones separately", async () => {
    const planned = await planBatchImport(
      readerFor([found("codex"), missing("cursor"), found("claude-code")]),
    );
    expect(planned.state).toBe("preview");
    if (planned.state !== "preview") return;
    expect(planned.candidates.map((c) => c.source_id)).toEqual([
      "codex",
      "claude-code",
    ]);
    expect(planned.notFound).toContain("cursor");
  });

  it("reports no_sources when nothing is detected", async () => {
    const planned = await planBatchImport(readerFor([missing("codex")]));
    expect(planned.state).toBe("no_sources");
  });
});

describe("A19 batch execution", () => {
  it("calls /sources/connect once with every source, not a per-source loop", async () => {
    const planned = await planBatchImport(
      readerFor([found("codex"), found("cursor")]),
    );
    if (planned.state !== "preview") throw new Error("expected preview");
    const post = vi.fn(
      async (_path: string, body: Record<string, unknown>) => ({
        state: "committed" as const,
        data: {
          request_id: body.request_id,
          accepted_source_ids: ["codex", "cursor"],
          failed_sources: {},
          jobs: [
            {
              job_id: "j1",
              source_id: "codex",
              status: "pending",
              discovered: 3,
              stored: 3,
              errors: 0,
              operation: "history",
            },
            {
              job_id: "j2",
              source_id: "cursor",
              status: "pending",
              discovered: 5,
              stored: 5,
              errors: 0,
              operation: "history",
            },
          ],
        },
      }),
    );
    const operations = { post } as unknown as CoreOperations;
    const document = await executeBatchImport(
      planned.candidates,
      readerFor([]),
      operations,
      planned.notFound,
      { confirmOverrides: false },
    );
    // Exactly one request for two sources — the batch endpoint, per contract.
    expect(post).toHaveBeenCalledTimes(1);
    const [path, body] = post.mock.calls[0]!;
    expect(path).toBe("/api/v1/sources/connect");
    expect(body.source_ids).toEqual(["codex", "cursor"]);
    expect(body.enable_sync).toBe(true);
    expect(document.state).toBe("committed");
    expect(document.sources.map((row) => row.source_id)).toEqual([
      "codex",
      "cursor",
    ]);
    expect(batchScanExitCode(document)).toBe(0);
  });

  it("splits a partial success row by row and exits non-zero", async () => {
    const planned = await planBatchImport(
      readerFor([found("codex"), found("cursor")]),
    );
    if (planned.state !== "preview") throw new Error("expected preview");
    const operations = {
      post: vi.fn(async (_path: string, body: Record<string, unknown>) => ({
        state: "committed" as const,
        data: {
          request_id: body.request_id,
          accepted_source_ids: ["codex"],
          failed_sources: { cursor: "source_identity_changed" },
          jobs: [
            {
              job_id: "j1",
              source_id: "codex",
              status: "pending",
              discovered: 1,
              stored: 1,
              errors: 0,
              operation: "history",
            },
          ],
        },
      })),
    } as unknown as CoreOperations;
    const document = await executeBatchImport(
      planned.candidates,
      readerFor([]),
      operations,
      planned.notFound,
      { confirmOverrides: false },
    );
    expect(document.state).toBe("partial");
    const byId = Object.fromEntries(
      document.sources.map((row) => [row.source_id, row]),
    );
    expect(byId.codex?.status).toBe("imported");
    expect(byId.cursor?.status).toBe("failed");
    expect(byId.cursor?.failure_reason).toBe("source_identity_changed");
    expect(document.accepted_source_ids).toEqual(["codex"]);
    // Partial success must be distinguishable by exit code.
    expect(batchScanExitCode(document)).toBe(1);
    expect(renderBatchScanText(document)).toContain("部分完成");
  });

  // A fresh batch request that is refused because imports are already in
  // flight is a refusal, not a committed success. Same-request recovery is
  // handled only by the single-source receipt lookup path.
  it("rejects a fresh all-busy batch as source_import_all_busy", async () => {
    const planned = await planBatchImport(
      readerFor([found("codex"), found("cursor")]),
    );
    if (planned.state !== "preview") throw new Error("expected preview");
    const operations = {
      post: vi.fn(async (_path: string, body: Record<string, unknown>) => ({
        state: "committed" as const,
        data: {
          request_id: body.request_id,
          accepted_source_ids: [],
          failed_sources: {
            codex: "source_import_busy",
            cursor: "source_import_busy",
          },
          jobs: [],
        },
      })),
    } as unknown as CoreOperations;
    const document = await executeBatchImport(
      planned.candidates,
      readerFor([]),
      operations,
      [],
      { confirmOverrides: false },
    );
    expect(document.sources.every((row) => row.status === "failed")).toBe(true);
    expect(
      document.sources.every(
        (row) => row.failure_reason === "source_import_busy",
      ),
    ).toBe(true);
    expect(document.state).toBe("rejected");
    expect(document.code).toBe("source_import_all_busy");
    expect(batchScanExitCode(document)).toBe(1);
  });

  it("reports a mixed accepted and busy batch as partial", async () => {
    const planned = await planBatchImport(
      readerFor([found("codex"), found("cursor")]),
    );
    if (planned.state !== "preview") throw new Error("expected preview");
    const operations = {
      post: vi.fn(async (_path: string, body: Record<string, unknown>) => ({
        state: "committed" as const,
        data: {
          request_id: body.request_id,
          accepted_source_ids: ["codex"],
          failed_sources: { cursor: "source_import_busy" },
          jobs: [
            {
              job_id: "j1",
              source_id: "codex",
              status: "pending",
              discovered: 1,
              stored: 1,
              errors: 0,
              operation: "history",
            },
          ],
        },
      })),
    } as unknown as CoreOperations;
    const document = await executeBatchImport(
      planned.candidates,
      readerFor([]),
      operations,
      [],
      { confirmOverrides: false },
    );
    const busy = document.sources.find((row) => row.source_id === "cursor");
    expect(document.state).toBe("partial");
    expect(busy?.status).toBe("failed");
    expect(busy?.failure_reason).toBe("source_import_busy");
    expect(batchScanExitCode(document)).toBe(1);
  });

  it("marks a not_found source in the receipt so the gap is visible", async () => {
    const planned = await planBatchImport(
      readerFor([found("codex"), missing("cursor")]),
    );
    if (planned.state !== "preview") throw new Error("expected preview");
    const operations = {
      post: vi.fn(async (_path: string, body: Record<string, unknown>) => ({
        state: "committed" as const,
        data: {
          request_id: body.request_id,
          accepted_source_ids: ["codex"],
          failed_sources: {},
          jobs: [
            {
              job_id: "j1",
              source_id: "codex",
              status: "pending",
              discovered: 1,
              stored: 1,
              errors: 0,
              operation: "history",
            },
          ],
        },
      })),
    } as unknown as CoreOperations;
    const document = await executeBatchImport(
      planned.candidates,
      readerFor([]),
      operations,
      planned.notFound,
      { confirmOverrides: false },
    );
    const notFoundRow = document.sources.find(
      (row) => row.source_id === "cursor",
    );
    expect(notFoundRow?.status).toBe("not_found");
    expect(document.not_found_source_ids).toContain("cursor");
  });
});

describe("environment-override sources need --confirm-overrides", () => {
  it("leaves them out and names them unless confirmed", async () => {
    const planned = await planBatchImport(
      readerFor([
        found("codex"),
        found("dsh", {
          candidate_kind: "environment_override",
          requires_override_confirmation: true,
        }),
      ]),
    );
    if (planned.state !== "preview") throw new Error("expected preview");
    const post = vi.fn(
      async (_path: string, body: Record<string, unknown>) => ({
        state: "committed" as const,
        data: {
          request_id: body.request_id,
          accepted_source_ids: body.source_ids,
          failed_sources: {},
          jobs: [],
        },
      }),
    );
    const operations = { post } as unknown as CoreOperations;
    const skipped = await executeBatchImport(
      planned.candidates,
      readerFor([]),
      operations,
      planned.notFound,
      { confirmOverrides: false },
    );
    expect(post.mock.calls[0]![1]).toMatchObject({
      source_ids: ["codex"],
      confirm_environment_overrides: false,
    });
    expect(skipped.override_required_source_ids).toEqual(["dsh"]);
    expect(renderBatchScanText(skipped)).toContain("--confirm-overrides");

    const confirmed = await executeBatchImport(
      planned.candidates,
      readerFor([]),
      operations,
      planned.notFound,
      { confirmOverrides: true },
    );
    expect(post.mock.calls[1]![1]).toMatchObject({
      source_ids: ["codex", "dsh"],
      confirm_environment_overrides: true,
    });
    expect(confirmed.override_required_source_ids).toBeUndefined();
  });

  it("refuses without a request when every detected source needs confirmation", async () => {
    const planned = await planBatchImport(
      readerFor([
        found("dsh", {
          candidate_kind: "environment_override",
          requires_override_confirmation: true,
        }),
      ]),
    );
    if (planned.state !== "preview") throw new Error("expected preview");
    const post = vi.fn();
    const document = await executeBatchImport(
      planned.candidates,
      readerFor([]),
      { post } as unknown as CoreOperations,
      planned.notFound,
      { confirmOverrides: false },
    );
    expect(post).not.toHaveBeenCalled();
    expect(document).toMatchObject({
      state: "rejected",
      code: "environment_override_confirmation_required",
    });
    expect(batchScanExitCode(document)).toBe(1);
  });
});
