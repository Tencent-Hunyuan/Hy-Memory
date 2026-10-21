import { mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { run } from "./cli.js";
import type { CoreReader } from "./core-client.js";
import type { CoreOperations } from "./core-operations.js";
import {
  executeMemoryMutation,
  inspectMemoryPlan,
  memoryPlanAcceptsContent,
  planExistingMemoryMutation,
  planMemoryAdd,
  readMemoryMutationReceipt,
} from "./memory-flows.js";
import { readMemoryDetail } from "./memory-report.js";

const transport = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
}));

vi.mock("axios", () => ({
  default: { create: vi.fn(() => transport), get: vi.fn() },
}));

const runtime = vi.hoisted(() => ({
  inspectRuntimeStatus: vi.fn(),
  discoverRuntimeAttachment: vi.fn(),
  stopRuntimeProcess: vi.fn(),
}));

vi.mock("@one-memory/runtime-control", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@one-memory/runtime-control")>()),
  ...runtime,
}));

const MEMORY = {
  memory_id: "memory-1",
  user_id: "local",
  kind: "working",
  status: "confirmed",
  content: "Existing synthetic memory.",
  scopes: ["all-apps"],
  evidence_ids: ["evidence-1"],
  origin: "extracted",
  tags: ["synthetic"],
  revision: 3,
  created_at: "2026-09-19T00:00:00Z",
  updated_at: "2026-09-19T00:00:01Z",
  deleted_at: null,
};

const DETAIL = {
  memory: MEMORY,
  evidence: [
    {
      evidence_id: "evidence-1",
      available: true,
      source_id: "codex",
      written_by: "person",
      created_at: "2026-09-19T00:00:00Z",
      verbatim: "Synthetic source text.",
    },
  ],
  history: [
    {
      revision: 3,
      action: "create",
      status: "confirmed",
      content: "Existing synthetic memory.",
      scopes: ["all-apps"],
      created_at: "2026-09-19T00:00:01Z",
    },
  ],
  index_sync: null,
};

const ATTACHED = {
  instanceId: "instance-1",
  apiToken: "synthetic-runtime-token",
  host: "127.0.0.1" as const,
  port: 8765,
  servicePid: 4242,
  health: {
    status: "ok",
    service: "one-memory-core",
    version: "1.2.3",
    instance_id: "instance-1",
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  runtime.inspectRuntimeStatus.mockResolvedValue({
    state: "running",
    code: "runtime_verified",
    instanceId: "instance-1",
    host: "127.0.0.1",
    port: 8765,
    version: "1.2.3",
  });
  runtime.discoverRuntimeAttachment.mockResolvedValue(ATTACHED);
  transport.get.mockImplementation(async (url: string) =>
    url === "/api/v1/memories/memory-1"
      ? { status: 200, data: DETAIL }
      : { status: 404, data: {} },
  );
});

describe("memory governance plans", () => {
  it("plans a manual memory without putting private content in the plan", () => {
    const result = planMemoryAdd({
      content: "Private synthetic addition.",
      memoryKind: "working",
      scopes: ["all-apps"],
      evidenceIds: ["evidence-1"],
    });
    expect(result.state).toBe("preview");
    expect(JSON.stringify(result)).not.toContain("Private synthetic addition.");
    if (result.state !== "preview") throw new Error("preview expected");
    expect(result.plan).toMatchObject({
      operation: "add",
      memory_kind: "working",
      scopes: ["all-apps"],
      evidence_ids: ["evidence-1"],
      content_characters: 27,
    });
    expect(inspectMemoryPlan(result.plan)).toMatchObject({
      operationId: result.plan.request_id,
      needsContent: true,
    });
    expect(
      memoryPlanAcceptsContent(result.plan, "Private synthetic addition."),
    ).toBe(true);
    expect(memoryPlanAcceptsContent(result.plan, "Different content.")).toBe(
      false,
    );
  });

  it("binds correction to the current version, source and author", async () => {
    const result = await planExistingMemoryMutation(detailReader(), {
      operation: "correct",
      memoryId: "memory-1",
      content: "Corrected synthetic memory.",
    });
    expect(result.state).toBe("preview");
    if (result.state !== "preview") throw new Error("preview expected");
    expect(result.plan).toMatchObject({
      memory_id: "memory-1",
      expected_revision: 3,
      expected_status: "confirmed",
      expected_origin: "extracted",
      current: {
        revision: 3,
        evidence: [
          {
            evidence_id: "evidence-1",
            source_id: "codex",
            written_by: "person",
          },
        ],
      },
    });
    expect(JSON.stringify(result)).not.toContain("Corrected synthetic memory.");
  });

  it("posts a confirmed plan once and validates the bound receipt", async () => {
    const preview = planMemoryAdd({
      content: "Private synthetic addition.",
      memoryKind: "working",
      scopes: ["all-apps"],
      evidenceIds: ["evidence-1"],
    });
    if (preview.state !== "preview") throw new Error("preview expected");
    const post = vi.fn().mockResolvedValue({
      state: "committed",
      data: {
        ...MEMORY,
        memory_id: "created-1",
        content: "Private synthetic addition.",
        status: "confirmed",
        origin: "manual",
        revision: 1,
      },
    });
    const result = await executeMemoryMutation(
      preview.plan,
      "Private synthetic addition.",
      { post } as CoreOperations,
    );
    expect(result.state).toBe("committed");
    expect(post).toHaveBeenCalledWith(
      "/api/v1/memories",
      expect.objectContaining({
        request_id: preview.plan.request_id,
        draft: expect.objectContaining({
          origin: "manual",
          content: "Private synthetic addition.",
        }),
      }),
    );
    expect(JSON.stringify(result)).not.toContain("Private synthetic addition.");
  });

  it("does not take a created memory that is not in effect as the add", async () => {
    const preview = planMemoryAdd({
      content: "Private synthetic addition.",
      memoryKind: "working",
      scopes: ["all-apps"],
      evidenceIds: ["evidence-1"],
    });
    if (preview.state !== "preview") throw new Error("preview expected");
    const result = await executeMemoryMutation(
      preview.plan,
      "Private synthetic addition.",
      {
        post: vi.fn().mockResolvedValue({
          state: "committed",
          data: {
            ...MEMORY,
            memory_id: "created-1",
            content: "Private synthetic addition.",
            status: "deleted",
            origin: "manual",
            revision: 1,
          },
        }),
      } as CoreOperations,
    );
    expect(result).toMatchObject({
      state: "unknown",
      code: "memory_receipt_invalid",
    });
  });

  it.each([["delete", "/actions/delete", "deleted"]] as const)(
    "executes %s against the revision-bound action route",
    async (operation, suffix, status) => {
      const preview = await planExistingMemoryMutation(detailReader(), {
        operation,
        memoryId: "memory-1",
      });
      if (preview.state !== "preview") throw new Error("preview expected");
      const post = vi.fn().mockResolvedValue({
        state: "committed",
        data: { ...MEMORY, status, revision: 4 },
      });
      const result = await executeMemoryMutation(preview.plan, undefined, {
        post,
      } as CoreOperations);
      expect(result.state).toBe("committed");
      expect(post).toHaveBeenCalledWith(`/api/v1/memories/memory-1${suffix}`, {
        request_id: preview.plan.request_id,
        expected_revision: 3,
      });
    },
  );

  it("executes correction without changing origin or recall policy", async () => {
    const content = "Corrected synthetic memory.";
    const preview = await planExistingMemoryMutation(detailReader(), {
      operation: "correct",
      memoryId: "memory-1",
      content,
    });
    if (preview.state !== "preview") throw new Error("preview expected");
    const post = vi.fn().mockResolvedValue({
      state: "committed",
      data: {
        ...MEMORY,
        content,
        revision: 4,
      },
    });
    const result = await executeMemoryMutation(preview.plan, content, {
      post,
    } as CoreOperations);
    expect(result.state).toBe("committed");
    expect(post.mock.calls[0][0]).toBe("/api/v1/memories/memory-1/correct");
    expect(post.mock.calls[0][1]).toMatchObject({
      request_id: preview.plan.request_id,
      expected_revision: 3,
    });
  });

  it("treats a misbound HTTP 200 receipt as unknown", async () => {
    const preview = await planExistingMemoryMutation(detailReader(), {
      operation: "delete",
      memoryId: "memory-1",
    });
    if (preview.state !== "preview") throw new Error("preview expected");
    const result = await executeMemoryMutation(preview.plan, undefined, {
      post: vi.fn().mockResolvedValue({
        state: "committed",
        data: {
          ...MEMORY,
          memory_id: "another-memory",
          status: "deleted",
          revision: 4,
        },
      }),
    });
    expect(result).toMatchObject({
      state: "unknown",
      code: "memory_receipt_invalid",
      operation_id: preview.plan.request_id,
    });
  });

  it("recovers only an exactly bound governance transaction receipt", async () => {
    const preview = await planExistingMemoryMutation(detailReader(), {
      operation: "delete",
      memoryId: "memory-1",
    });
    if (preview.state !== "preview") throw new Error("preview expected");
    const result = await readMemoryMutationReceipt(
      receiptReader({
        schema_version: 2,
        request_id: preview.plan.request_id,
        state: "committed",
        operation: "delete",
        memory_id: "memory-1",
        result_revision: 4,
        result_status: "deleted",
        memory_state: "deleted",
        migrated_from: null,
      }),
      preview.plan,
    );
    expect(result).toMatchObject({
      state: "recovered",
      operation_id: preview.plan.request_id,
      receipt: {
        operation: "delete",
        memory_id: "memory-1",
        result_revision: 4,
        result_status: "deleted",
      },
    });
    expect(JSON.stringify(result)).not.toContain("content_sha256");
  });

  it.each([
    ["operation", "correct"],
    ["memory_id", "memory-2"],
    ["result_revision", 5],
    ["result_status", "confirmed"],
    ["migrated_from", "reject"],
  ] as const)("refuses a receipt with wrong %s", async (field, wrong) => {
    const preview = await planExistingMemoryMutation(detailReader(), {
      operation: "delete",
      memoryId: "memory-1",
    });
    if (preview.state !== "preview") throw new Error("preview expected");
    const result = await readMemoryMutationReceipt(
      receiptReader({
        schema_version: 2,
        request_id: preview.plan.request_id,
        state: "committed",
        operation: "delete",
        memory_id: "memory-1",
        result_revision: 4,
        result_status: "deleted",
        memory_state: "deleted",
        migrated_from: null,
        [field]: wrong,
      }),
      preview.plan,
    );
    expect(result).toMatchObject({
      state: "unknown",
      code: "memory_mutation_receipt_invalid",
    });
  });

  it("keeps not-found, denied and unavailable lookups unresolved", async () => {
    const preview = await planExistingMemoryMutation(detailReader(), {
      operation: "delete",
      memoryId: "memory-1",
    });
    if (preview.state !== "preview") throw new Error("preview expected");
    const notFound = await readMemoryMutationReceipt(
      receiptReader({
        schema_version: 2,
        request_id: preview.plan.request_id,
        state: "not_found",
        operation: null,
        memory_id: null,
        result_revision: null,
        result_status: null,
        memory_state: null,
        migrated_from: null,
      }),
      preview.plan,
    );
    expect(notFound).toMatchObject({
      state: "unknown",
      code: "memory_mutation_receipt_not_found_after_attempt",
    });
    for (const state of ["denied", "unavailable"] as const) {
      const result = await readMemoryMutationReceipt(
        async () => ({ state, code: `synthetic_${state}` }),
        preview.plan,
      );
      expect(result).toMatchObject({
        state: "unknown",
        code: `memory_mutation_receipt_synthetic_${state}`,
      });
    }
  });

  it("maps an add plan only to the Core create receipt", async () => {
    const preview = planMemoryAdd({
      content: "Private synthetic addition.",
      memoryKind: "working",
      scopes: ["all-apps"],
      evidenceIds: ["evidence-1"],
    });
    if (preview.state !== "preview") throw new Error("preview expected");
    const result = await readMemoryMutationReceipt(
      receiptReader({
        schema_version: 2,
        request_id: preview.plan.request_id,
        state: "committed",
        operation: "create",
        memory_id: "created-1",
        result_revision: 1,
        result_status: "confirmed",
        memory_state: "present",
        migrated_from: null,
      }),
      preview.plan,
    );
    expect(result).toMatchObject({
      state: "recovered",
      receipt: {
        operation: "create",
        memory_id: "created-1",
        result_revision: 1,
        result_status: "confirmed",
      },
    });
  });

  it("shows bounded history fields including scopes and status", async () => {
    const result = await readMemoryDetail(detailReader(), "memory-1");
    expect(result.history[0]).toMatchObject({
      revision: 3,
      action: "create",
      status: "confirmed",
      scopes: ["all-apps"],
      content: "Existing synthetic memory.",
    });
  });
});

describe("memory pending fence", () => {
  it("rejects missing confirmation and invalid plans before any write", async () => {
    for (const argv of [
      ["memories", "apply", "--plan-file", "/synthetic/plan", "--json"],
      ["memories", "plan", "delete", "graph", "--json"],
      ["memories", "plan", "delete", "memory-1", "--scope", "codex", "--json"],
      ["memories", "plan", "confirm", "memory-1", "--json"],
      ["memories", "plan", "reject", "memory-1", "--json"],
      [
        "memories",
        "plan",
        "add",
        "--content-file",
        "/synthetic/content",
        "--scope",
        "all-apps",
        "--json",
      ],
    ]) {
      const streams = captureStreams();
      try {
        expect(await run(argv)).toBe(2);
      } finally {
        streams.restore();
      }
    }
    expect(transport.post).not.toHaveBeenCalled();
  });

  it("generates a bounded correction preview without writing", async () => {
    const directory = await mkdtemp(
      path.join(os.tmpdir(), "om-memory-preview-"),
    );
    const contentFile = path.join(directory, "content.txt");
    await writeFile(contentFile, "Corrected synthetic memory.");
    const streams = captureStreams();
    try {
      expect(
        await run([
          "memories",
          "plan",
          "correct",
          "memory-1",
          "--content-file",
          contentFile,
          "--json",
        ]),
      ).toBe(0);
    } finally {
      streams.restore();
      await rm(directory, { recursive: true, force: true });
    }
    const output = JSON.parse(streams.stdout());
    expect(output).toMatchObject({
      state: "preview",
      plan: {
        operation: "correct",
        memory_id: "memory-1",
        expected_revision: 3,
      },
    });
    expect(streams.stdout()).not.toContain("Corrected synthetic memory.");
    expect(transport.post).not.toHaveBeenCalled();
  });

  it("checks content identity before claiming the durable fence", async () => {
    const preview = planMemoryAdd({
      content: "Private synthetic addition.",
      memoryKind: "working",
      scopes: ["all-apps"],
      evidenceIds: ["evidence-1"],
    });
    if (preview.state !== "preview") throw new Error("preview expected");
    const directory = await mkdtemp(
      path.join(os.tmpdir(), "om-memory-content-"),
    );
    const planFile = path.join(directory, "plan.json");
    const wrongFile = path.join(directory, "wrong.txt");
    const correctFile = path.join(directory, "correct.txt");
    await writeFile(planFile, JSON.stringify(preview));
    await writeFile(wrongFile, "Different content.");
    await writeFile(correctFile, "Private synthetic addition.");
    const wrong = captureStreams();
    try {
      expect(
        await run([
          "memories",
          "apply",
          "--plan-file",
          planFile,
          "--content-file",
          wrongFile,
          "--confirm",
          "--json",
        ]),
      ).toBe(2);
    } finally {
      wrong.restore();
    }
    transport.post.mockResolvedValue({
      status: 200,
      data: {
        ...MEMORY,
        memory_id: "created-1",
        content: "Private synthetic addition.",
        status: "confirmed",
        origin: "manual",
        revision: 1,
      },
    });
    const correct = captureStreams();
    try {
      expect(
        await run([
          "memories",
          "apply",
          "--plan-file",
          planFile,
          "--content-file",
          correctFile,
          "--confirm",
          "--json",
        ]),
      ).toBe(0);
    } finally {
      correct.restore();
      await rm(directory, { recursive: true, force: true });
    }
    expect(transport.post).toHaveBeenCalledTimes(1);
  });

  it("recovers an unknown mutation after restart without reposting", async () => {
    const preview = await planExistingMemoryMutation(detailReader(), {
      operation: "delete",
      memoryId: "memory-1",
    });
    if (preview.state !== "preview") throw new Error("preview expected");
    const directory = await mkdtemp(path.join(os.tmpdir(), "om-memory-plan-"));
    const planFile = path.join(directory, "plan.json");
    await writeFile(planFile, JSON.stringify(preview));
    transport.post.mockRejectedValue(new Error("synthetic disconnect"));
    let receiptLookups = 0;
    transport.get.mockImplementation(async (url: string) => {
      if (url === "/api/v1/memories/memory-1") {
        return { status: 200, data: DETAIL };
      }
      if (url === `/api/v1/memory-mutations/${preview.plan.request_id}`) {
        receiptLookups += 1;
        if (receiptLookups === 1) throw new Error("synthetic lookup outage");
        return {
          status: 200,
          data: {
            schema_version: 2,
            request_id: preview.plan.request_id,
            state: "committed",
            operation: "delete",
            memory_id: "memory-1",
            result_revision: 4,
            result_status: "deleted",
            memory_state: "deleted",
            migrated_from: null,
          },
        };
      }
      return { status: 404, data: {} };
    });
    const first = captureStreams();
    try {
      expect(
        await run([
          "memories",
          "apply",
          "--plan-file",
          planFile,
          "--confirm",
          "--json",
        ]),
      ).toBe(1);
    } finally {
      first.restore();
    }
    expect(JSON.parse(first.stdout())).toMatchObject({
      state: "unknown",
      code: "memory_mutation_receipt_transport_failed",
    });
    const second = captureStreams();
    try {
      expect(
        await run([
          "memories",
          "apply",
          "--plan-file",
          planFile,
          "--confirm",
          "--json",
        ]),
      ).toBe(0);
    } finally {
      second.restore();
      await rm(directory, { recursive: true, force: true });
    }
    expect(JSON.parse(second.stdout())).toMatchObject({
      state: "recovered",
      receipt: {
        operation: "delete",
        memory_id: "memory-1",
        result_revision: 4,
        result_status: "deleted",
      },
    });
    expect(transport.post).toHaveBeenCalledTimes(1);
    expect(receiptLookups).toBe(2);
  });
});

function captureStreams() {
  let stdout = "";
  let stderr = "";
  const out = vi.spyOn(process.stdout, "write").mockImplementation((chunk) => {
    stdout += String(chunk);
    return true;
  });
  const error = vi
    .spyOn(process.stderr, "write")
    .mockImplementation((chunk) => {
      stderr += String(chunk);
      return true;
    });
  return {
    stdout: () => stdout,
    stderr: () => stderr,
    restore: () => {
      out.mockRestore();
      error.mockRestore();
    },
  };
}

function detailReader(): CoreReader {
  return async <T>() => ({ state: "ok", data: DETAIL as T });
}

function receiptReader(receipt: unknown): CoreReader {
  return async <T>() => ({ state: "ok", data: receipt as T });
}
