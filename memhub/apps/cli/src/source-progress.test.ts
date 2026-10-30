import { beforeEach, describe, expect, it, vi } from "vitest";

import { run } from "./cli.js";

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

const RUNNING = {
  state: "running" as const,
  code: "runtime_verified",
  instanceId: "instance-1",
  host: "127.0.0.1",
  port: 8765,
  version: "1.2.3",
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

const SOURCE = {
  source_id: "codex",
  display_name: "Codex",
  storage_kinds: ["history"],
  available: true,
  authorized: true,
  detected: true,
  root_label: "Codex history",
  candidate_path: "/synthetic/not-read",
  candidate_kind: "default",
  imported_messages: 20,
  imported_conversations: 3,
  capabilities: {
    historical_import: "implemented",
    incremental_import: "implemented",
    live_sync: "implemented",
    recall: "implemented",
    installer: "not_implemented",
    verification: "fixture_only",
  },
  sync_completeness: "additions_only",
  sync_enabled: true,
  location_state: "found",
  grant_revision: 4,
  root_id: `root:v1:${"b".repeat(64)}`,
};

const RESPONSES = new Map<string, { status: number; data: unknown }>([
  ["/api/v1/sources", { status: 200, data: [SOURCE] }],
  [
    "/api/v1/live-sync",
    {
      status: 200,
      data: [
        {
          source_id: "codex",
          mode: "watch_and_polling",
          state: "degraded",
          last_job_id: "job-running",
          last_attempt_at: "T2",
          last_success_at: "T1",
          last_error_code: "source_temporarily_unreadable",
        },
      ],
    },
  ],
  [
    "/api/v1/import-jobs",
    {
      status: 200,
      data: [
        importJob("job-running", "running"),
        importJob("job-paused", "paused"),
        importJob("job-failed", "failed"),
      ],
    },
  ],
  [
    "/api/v1/memory-processing",
    {
      status: 200,
      data: {
        pending: 4,
        fresh_pending: 1,
        backlog_pending: 3,
        outside_window_pending: 2,
        recent_completions: ["T1"],
        running: 1,
        waiting_for_model: 2,
        completed: 8,
        failed: 1,
        blocked_stages: [
          { kind: "extract", reason: "model_missing", count: 2 },
        ],
        seconds_per_completion: null,
        extraction_window_preset: "week",
        manual_extract_batches_planned: 0,
        manual_extract_batches_current: 0,
      },
    },
  ],
  [
    "/api/v1/import-jobs/job-running",
    { status: 200, data: importJob("job-running", "running") },
  ],
  [
    "/api/v1/import-jobs/job-running/issues",
    {
      status: 200,
      data: [
        {
          issue_id: 7,
          job_id: "job-running",
          kind: "error",
          conversation_id: "conversation-synthetic",
          reason: "record_invalid",
          created_at: "T2",
        },
      ],
    },
  ],
]);

beforeEach(() => {
  vi.clearAllMocks();
  runtime.inspectRuntimeStatus.mockResolvedValue(RUNNING);
  runtime.discoverRuntimeAttachment.mockResolvedValue(ATTACHED);
  transport.get.mockImplementation(async (url: string) => {
    return RESPONSES.get(url) ?? { status: 404, data: {} };
  });
});

describe("CLI-07/10 source and synchronization reads", () => {
  it("lists source capability and authorization without detecting or scanning", async () => {
    const streams = captureStreams();
    try {
      expect(await run(["sources", "list", "--json"], {})).toBe(0);
    } finally {
      streams.restore();
    }
    const report = JSON.parse(streams.stdout());
    expect(report.state).toBe("ok");
    expect(report.sources[0]).toMatchObject({
      source_id: "codex",
      display_name: "Codex",
      available: true,
      authorized: true,
      imported_messages: 20,
      sync_enabled: true,
      sync_completeness: "additions_only",
    });
    expect(requestedPaths()).toEqual(["/api/v1/sources"]);
    expect(requestedPaths().some((path) => path.includes("detect"))).toBe(
      false,
    );
  });

  it("keeps degraded synchronization distinct and prints its error code", async () => {
    const streams = captureStreams();
    try {
      expect(await run(["sync", "status", "--json"], {})).toBe(1);
    } finally {
      streams.restore();
    }
    const report = JSON.parse(streams.stdout());
    expect(report.state).toBe("partial");
    expect(report.sources[0]).toMatchObject({
      source_id: "codex",
      state: "degraded",
      last_error_code: "source_temporarily_unreadable",
    });
    expect(requestedPaths()).toEqual(["/api/v1/live-sync"]);
  });

  it("fails closed on malformed source and synchronization DTOs", async () => {
    for (const [path, argv] of [
      ["/api/v1/sources", ["sources", "list", "--json"]],
      ["/api/v1/live-sync", ["sync", "status", "--json"]],
    ] as const) {
      transport.get.mockImplementation(async (url: string) =>
        url === path
          ? {
              status: 200,
              data:
                path === "/api/v1/sources"
                  ? [{ ...SOURCE, display_name: { token: "nested-secret" } }]
                  : [
                      {
                        source_id: "codex",
                        mode: "polling",
                        state: "invented-state",
                        last_job_id: null,
                        last_attempt_at: null,
                        last_success_at: null,
                        last_error_code: null,
                      },
                    ],
            }
          : { status: 404, data: {} },
      );
      const streams = captureStreams();
      try {
        expect(await run(argv, {})).toBe(1);
      } finally {
        streams.restore();
      }
      expect(JSON.parse(streams.stdout())).toMatchObject({
        state: "invalid",
        code: "response_invalid",
      });
      expect(streams.stdout()).not.toContain("nested-secret");
      vi.clearAllMocks();
      runtime.inspectRuntimeStatus.mockResolvedValue(RUNNING);
      runtime.discoverRuntimeAttachment.mockResolvedValue(ATTACHED);
    }
  });
});

describe("CLI-19 progress and issue reads", () => {
  it("keeps import and generation states distinct without inventing a percentage", async () => {
    const streams = captureStreams();
    try {
      expect(await run(["progress", "--json"], {})).toBe(1);
    } finally {
      streams.restore();
    }
    const report = JSON.parse(streams.stdout());
    expect(report.state).toBe("partial");
    expect(
      report.imports.jobs.map((job: { status: string }) => job.status),
    ).toEqual(["running", "paused", "failed"]);
    expect(report.generation.summary).toMatchObject({
      pending: 4,
      running: 1,
      waiting_for_model: 2,
      failed: 1,
    });
    expect(report.imports).toMatchObject({
      imported_conversations: 3,
      imported_messages: 20,
      totals_state: { state: "ok" },
    });
    // The retired per-task list is no longer read or reported.
    expect(report.generation).not.toHaveProperty("tasks");
    expect(requestedPaths()).not.toContain("/api/v1/memory-tasks");
    expect(streams.stdout()).not.toMatch(/percent|percentage|%/i);
  });

  it("prints the same cumulative source totals as status", async () => {
    const streams = captureStreams();
    try {
      expect(await run(["progress"], {})).toBe(1);
    } finally {
      streams.restore();
    }
    expect(streams.stdout()).toContain("累计导入：3 个会话 · 20 条消息");
  });

  it("reads one import job and its bounded issues by explicit ID", async () => {
    const streams = captureStreams();
    try {
      expect(
        await run(
          [
            "progress",
            "--job",
            "job-running",
            "--issues",
            "--limit",
            "10",
            "--json",
          ],
          {},
        ),
      ).toBe(1);
    } finally {
      streams.restore();
    }
    const report = JSON.parse(streams.stdout());
    expect(report.imports.jobs).toHaveLength(1);
    expect(report.imports.jobs[0]).toMatchObject({
      job_id: "job-running",
      status: "running",
    });
    expect(report.imports.issues).toEqual([
      expect.objectContaining({
        issue_id: 7,
        job_id: "job-running",
        reason: "record_invalid",
      }),
    ]);
    expect(requestedPaths()).toContain(
      "/api/v1/import-jobs/job-running/issues",
    );
  });

  it("rejects issues bound to another import job", async () => {
    transport.get.mockImplementation(async (url: string) => {
      const response = RESPONSES.get(url) ?? { status: 404, data: {} };
      return url === "/api/v1/import-jobs/job-running/issues"
        ? {
            status: 200,
            data: [
              {
                issue_id: 8,
                job_id: "another-job",
                kind: "error",
                conversation_id: null,
                reason: "SYNTHETIC_FOREIGN_ISSUE",
                created_at: "T2",
              },
            ],
          }
        : response;
    });
    const streams = captureStreams();
    try {
      expect(
        await run(
          ["progress", "--job", "job-running", "--issues", "--json"],
          {},
        ),
      ).toBe(1);
    } finally {
      streams.restore();
    }
    const report = JSON.parse(streams.stdout());
    expect(report.imports.issues_state).toMatchObject({
      state: "invalid",
      code: "response_invalid",
    });
    expect(streams.stdout()).not.toContain("SYNTHETIC_FOREIGN_ISSUE");
  });

  it("bounds the import job list and emits an explicit truncation receipt", async () => {
    const streams = captureStreams();
    try {
      expect(await run(["progress", "--limit", "1", "--json"], {})).toBe(1);
    } finally {
      streams.restore();
    }
    const report = JSON.parse(streams.stdout());
    expect(report.imports.jobs).toHaveLength(1);
    expect(report.imports).toMatchObject({
      returned: 1,
      validated: 3,
      output_truncated: true,
    });
  });

  it("streams bounded progress snapshots as serial NDJSON", async () => {
    const streams = captureStreams();
    try {
      expect(
        await run(
          ["progress", "--watch", "--interval", "1", "--limit", "1", "--json"],
          {},
          {
            watchMaxEvents: 2,
            watchWait: async () => undefined,
            watchNow: () => "T",
          },
        ),
      ).toBe(0);
    } finally {
      streams.restore();
    }
    const events = streams
      .stdout()
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line));
    expect(events).toHaveLength(2);
    expect(events.map((event) => event.sequence)).toEqual([1, 2]);
    expect(events.every((event) => event.event === "progress")).toBe(true);
    expect(events.every((event) => event.data.imports.jobs.length === 1)).toBe(
      true,
    );
    for (const path of [
      "/api/v1/import-jobs",
      "/api/v1/sources",
      "/api/v1/memory-processing",
    ]) {
      expect(requestedPaths().filter((item) => item === path)).toHaveLength(2);
    }
    expect(runtime.stopRuntimeProcess).not.toHaveBeenCalled();
  });

  it("refreshes a terminal watch in place and explains how to stop it", async () => {
    const isTTY = Object.getOwnPropertyDescriptor(process.stdout, "isTTY");
    Object.defineProperty(process.stdout, "isTTY", {
      configurable: true,
      value: true,
    });
    const streams = captureStreams();
    try {
      expect(
        await run(
          ["progress", "--watch", "--interval", "1", "--limit", "1"],
          {},
          {
            watchMaxEvents: 2,
            watchWait: async () => undefined,
            watchNow: () => "15:04:05.067",
          },
        ),
      ).toBe(0);
    } finally {
      streams.restore();
      if (isTTY) {
        Object.defineProperty(process.stdout, "isTTY", isTTY);
      } else {
        delete (process.stdout as { isTTY?: boolean }).isTTY;
      }
    }
    const output = streams.stdout();
    expect(output).toContain(
      "每 1 秒刷新一次，按 Ctrl-C 退出观察，后台不会停止\n",
    );
    expect(output).toContain("[15:04:05.067] progress #1");
    expect(output).toContain("[15:04:05.067] progress #2");
    expect(output.match(new RegExp("\\x1b\\[2J\\x1b\\[H", "g"))).toHaveLength(
      2,
    );
  });

  it("keeps non-terminal watch output append-only", async () => {
    const streams = captureStreams();
    try {
      expect(
        await run(
          ["progress", "--watch", "--interval", "1", "--limit", "1"],
          {},
          {
            watchMaxEvents: 2,
            watchWait: async () => undefined,
            watchNow: () => "15:04:05.067",
          },
        ),
      ).toBe(0);
    } finally {
      streams.restore();
    }
    const output = streams.stdout();
    expect(output).not.toContain("按 Ctrl-C");
    expect(output).not.toContain("\x1b[");
    expect(output).toContain("[15:04:05.067] progress #1");
    expect(output).toContain("[15:04:05.067] progress #2");
  });

  it("preserves denied, unavailable and malformed sections in one partial report", async () => {
    transport.get.mockImplementation(async (url: string) => {
      if (url === "/api/v1/import-jobs") return { status: 503, data: {} };
      if (url === "/api/v1/sources") return { status: 403, data: {} };
      if (url === "/api/v1/memory-processing") {
        return { status: 200, data: { pending: { secret: "nested" } } };
      }
      return { status: 404, data: {} };
    });
    const streams = captureStreams();
    try {
      expect(await run(["progress", "--json"], {})).toBe(1);
    } finally {
      streams.restore();
    }
    const report = JSON.parse(streams.stdout());
    expect(report.imports).toMatchObject({
      state: "unavailable",
      code: "http_503",
    });
    expect(report.generation.summary_state).toEqual({
      state: "invalid",
      code: "response_invalid",
    });
    expect(report.imports.totals_state).toEqual({
      state: "denied",
      code: "http_403",
    });
    expect(streams.stdout()).not.toContain("nested");
  });

  it("rejects unsafe IDs and contradictory issue arguments before attachment", async () => {
    for (const argv of [
      ["progress", "--issues"],
      ["progress", "--job", "../sources"],
      ["progress", "--job", "job/one"],
      ["progress", "--limit", "0"],
      ["progress", "--limit", "101"],
      ["progress", "--interval", "1"],
      ["progress", "--watch", "--interval", "0"],
      ["progress", "--watch", "--interval", "301"],
      ["sources"],
      ["sync"],
    ]) {
      const streams = captureStreams();
      try {
        expect(await run(argv, {})).toBe(2);
      } finally {
        streams.restore();
      }
      expect(streams.stdout()).toBe("");
    }
    expect(runtime.discoverRuntimeAttachment).not.toHaveBeenCalled();
    expect(transport.get).not.toHaveBeenCalled();
  });

  it("issues GET only to the explicit read allowlist", async () => {
    await run(["sources", "list", "--json"], {});
    await run(["sync", "status", "--json"], {});
    await run(["progress", "--json"], {});
    await run(["progress", "--job", "job-running", "--issues", "--json"], {});
    for (const method of [
      transport.post,
      transport.put,
      transport.patch,
      transport.delete,
    ]) {
      expect(method).not.toHaveBeenCalled();
    }
    expect(transport.get.mock.calls.length).toBeGreaterThan(0);
    expect(
      requestedPaths().every((path) =>
        [
          "/api/v1/sources",
          "/api/v1/live-sync",
          "/api/v1/import-jobs",
          "/api/v1/memory-processing",
        ].some((allowed) => path === allowed || path.startsWith(`${allowed}/`)),
      ),
    ).toBe(true);
  });

  it("asks a stopped Runtime for nothing", async () => {
    runtime.inspectRuntimeStatus.mockResolvedValue({
      state: "stopped",
      code: "runtime_not_running",
    });
    for (const argv of [
      ["sources", "list", "--json"],
      ["sync", "status", "--json"],
      ["progress", "--json"],
    ]) {
      const streams = captureStreams();
      try {
        expect(await run(argv, {})).toBe(3);
      } finally {
        streams.restore();
      }
    }
    expect(runtime.discoverRuntimeAttachment).not.toHaveBeenCalled();
    expect(transport.get).not.toHaveBeenCalled();
  });
});

function importJob(
  jobId: string,
  status:
    | "pending"
    | "running"
    | "paused"
    | "interrupted"
    | "completed"
    | "failed"
    | "cancelled",
) {
  return {
    job_id: jobId,
    user_id: "local",
    source_id: "codex",
    operation: "history",
    status,
    phase: status === "completed" ? "done" : "read",
    checkpoint: null,
    discovered: 10,
    discovered_targets: 2,
    emitted: 8,
    stored: 7,
    revised: 1,
    merged: 0,
    skipped: 1,
    errors: status === "failed" ? 1 : 0,
    created_at: "T1",
    updated_at: "T2",
  };
}

function requestedPaths(): string[] {
  return (transport.get.mock.calls as [string][]).map(([path]) => path);
}

function captureStreams() {
  let out = "";
  let error = "";
  const originalOut = process.stdout.write;
  const originalError = process.stderr.write;
  process.stdout.write = ((chunk: string) => {
    out += chunk;
    return true;
  }) as typeof process.stdout.write;
  process.stderr.write = ((chunk: string) => {
    error += chunk;
    return true;
  }) as typeof process.stderr.write;
  return {
    stdout: () => out,
    stderr: () => error,
    restore: () => {
      process.stdout.write = originalOut;
      process.stderr.write = originalError;
    },
  };
}
