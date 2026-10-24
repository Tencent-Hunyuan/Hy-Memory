import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { run } from "./cli.js";
import { isReadOnlyPath } from "./core-client.js";

const transport = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
  request: vi.fn(),
}));

vi.mock("axios", () => ({
  // `create` backs the read/operation clients; the top-level `post` backs
  // `submitBackup`, which posts without an instance.
  default: {
    create: vi.fn(() => transport),
    get: vi.fn(),
    post: transport.post,
  },
}));

const runtime = vi.hoisted(() => ({
  inspectRuntimeStatus: vi.fn(),
  discoverRuntimeAttachment: vi.fn(),
  inspectBackgroundHealth: vi.fn(),
  stopRuntimeProcess: vi.fn(),
}));

vi.mock("@one-memory/runtime-control", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@one-memory/runtime-control")>()),
  ...runtime,
}));

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

const RUNNING = {
  state: "running" as const,
  code: "runtime_verified",
  instanceId: "instance-1",
  host: "127.0.0.1",
  port: 8765,
  version: "1.2.3",
};

const CORE = new Map<string, { status: number; data: unknown }>([
  [
    "/api/v1/sources",
    {
      status: 200,
      data: [
        {
          source_id: "codex",
          display_name: "Codex",
          storage_kinds: ["history"],
          available: true,
          authorized: true,
          detected: true,
          root_label: "Codex history",
          candidate_path: "/synthetic/codex",
          candidate_kind: "default",
          imported_conversations: 7,
          imported_messages: 120,
          capabilities: {
            historical_import: "implemented",
            incremental_import: "implemented",
            live_sync: "implemented",
            recall: "implemented",
            installer: "not_implemented",
            verification: "fixture_only",
          },
          sync_completeness: "additions_only",
          grant_revision: 3,
          root_id: `root:v1:${"a".repeat(64)}`,
          sync_enabled: true,
        },
        {
          source_id: "cursor",
          display_name: "Cursor",
          storage_kinds: ["history"],
          available: true,
          authorized: false,
          detected: true,
          root_label: "Cursor history",
          candidate_path: "/synthetic/cursor",
          candidate_kind: "default",
          imported_conversations: 0,
          imported_messages: 0,
          capabilities: {
            historical_import: "implemented",
            incremental_import: "not_implemented",
            live_sync: "not_implemented",
            recall: "implemented",
            installer: "not_implemented",
            verification: "fixture_only",
          },
          sync_completeness: "one_time_snapshot",
          grant_revision: 0,
          root_id: null,
          sync_enabled: false,
        },
      ],
    },
  ],
  [
    "/api/v1/live-sync",
    {
      status: 200,
      data: [
        {
          source_id: "codex",
          mode: "polling",
          state: "running",
          last_job_id: null,
          last_attempt_at: null,
          last_success_at: null,
          last_error_code: null,
        },
        {
          source_id: "cursor",
          mode: "polling",
          state: "degraded",
          last_job_id: null,
          last_attempt_at: null,
          last_success_at: null,
          last_error_code: "source_unreadable",
        },
      ],
    },
  ],
  [
    "/api/v1/memory-processing",
    {
      status: 200,
      data: {
        pending: 3,
        fresh_pending: 1,
        backlog_pending: 2,
        outside_window_pending: 0,
        running: 1,
        waiting_for_model: 2,
        completed: 9,
        failed: 1,
        blocked_stages: [
          { kind: "extract", reason: "model_missing", count: 2 },
        ],
      },
    },
  ],
  [
    "/api/v1/model-profiles",
    {
      status: 200,
      data: [
        {
          profile_id: "p1",
          name: "synthetic",
          provider_kind: "openai_compatible",
          model: "synthetic-model",
          has_api_key: true,
          credential_state: "available",
          is_active: true,
          // A server that leaks must not make the CLI leak.
          api_key: "sk-must-never-be-printed",
        },
      ],
    },
  ],
  [
    "/api/v1/home-summary",
    { status: 200, data: { memory_count: 42, latest: null, observed_at: "T" } },
  ],
]);

const READ_ONLY_PATHS = [
  "/api/v1/sources",
  "/api/v1/live-sync",
  "/api/v1/memory-processing",
  "/api/v1/model-profiles",
  "/api/v1/home-summary",
  "/api/v1/memory-library",
  "/api/v1/memories/",
  "/api/v2/memory-library",
  "/api/v2/memories/",
  "/api/v2/user-profile",
];

const BACKGROUND_HEALTHY = {
  state: "healthy" as const,
  code: "background_verified",
  core: "running" as const,
  broker: "running" as const,
  instanceId: "instance-1",
};

beforeEach(() => {
  vi.clearAllMocks();
  runtime.inspectRuntimeStatus.mockResolvedValue(RUNNING);
  runtime.discoverRuntimeAttachment.mockResolvedValue(ATTACHED);
  runtime.inspectBackgroundHealth.mockResolvedValue(BACKGROUND_HEALTHY);
  transport.get.mockImplementation(async (url: string) => {
    const hit = CORE.get(url);
    return hit ?? { status: 404, data: { detail: "not_found" } };
  });
});

describe("CLI-20 status summary", () => {
  it("reports every section from the authenticated read APIs", async () => {
    const streams = captureStreams();
    try {
      expect(await run(["status", "--json"], {})).toBe(0);
    } finally {
      streams.restore();
    }
    const report = JSON.parse(streams.stdout());
    expect(report.schema_version).toBe(1);
    expect(report.runtime).toMatchObject({
      state: "running",
      port: 8765,
      version: "1.2.3",
    });
    expect(report.sections.sources).toMatchObject({
      state: "ok",
      total: 2,
      authorized: 1,
      imported_conversations: 7,
    });
    expect(report.sections.sync).toMatchObject({ state: "ok", degraded: 1 });
    expect(report.sections.processing).toMatchObject({
      state: "ok",
      pending: 3,
      waiting_for_model: 2,
    });
    expect(report.sections.model).toMatchObject({ state: "ok", profiles: 1 });
    expect(report.sections.memories).toMatchObject({
      state: "ok",
      memory_count: 42,
    });
    expect(report.summary.state).toBe("ok");
    expect(streams.stderr()).toBe("");
  });

  it("doctor separates checks and marks unread pending operations not_checked", async () => {
    const streams = captureStreams();
    try {
      expect(await run(["doctor", "--json"], {})).toBe(1);
    } finally {
      streams.restore();
    }
    const report = JSON.parse(streams.stdout());
    expect(report.state).toBe("attention");
    expect(report.checks).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "core", state: "pass" }),
        expect.objectContaining({ id: "broker", state: "pass" }),
        expect.objectContaining({
          id: "source_permissions",
          state: "warn",
        }),
        expect.objectContaining({ id: "sync", state: "warn" }),
        expect.objectContaining({
          id: "pending_operations",
          state: "not_checked",
        }),
      ]),
    );
    expect(report).not.toHaveProperty("logs");
  });

  it("streams status as NDJSON without overlapping or stopping Runtime", async () => {
    const streams = captureStreams();
    try {
      expect(
        await run(
          ["status", "--watch", "--interval", "1", "--json"],
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
    expect(events.every((event) => event.event === "status")).toBe(true);
    expect(events.every((event) => event.instance_id === "instance-1")).toBe(
      true,
    );
    expect(runtime.inspectRuntimeStatus).toHaveBeenCalledTimes(2);
    expect(runtime.stopRuntimeProcess).not.toHaveBeenCalled();
    expect(
      (transport.get.mock.calls as [string, { signal?: AbortSignal }][]).every(
        ([, config]) => config.signal instanceof AbortSignal,
      ),
    ).toBe(true);
  });

  it("rejects invalid status watch intervals before Runtime inspection", async () => {
    for (const argv of [
      ["status", "--interval", "1"],
      ["status", "--watch", "--interval", "0"],
      ["status", "--watch", "--interval", "301"],
      ["doctor", "--watch"],
    ]) {
      const streams = captureStreams();
      try {
        expect(await run(argv, {})).toBe(2);
      } finally {
        streams.restore();
      }
    }
    expect(runtime.inspectRuntimeStatus).not.toHaveBeenCalled();
    expect(transport.get).not.toHaveBeenCalled();
  });

  // D-03 landed in a485f47, so the bridge is now read, not assumed. The guard
  // is no longer "always not_checked" but "never better than what Main's
  // inspector actually returned".
  it("reports the host bridge exactly as Main's inspector saw it", async () => {
    const healthy = captureStreams();
    try {
      expect(await run(["status", "--json"], {})).toBe(0);
    } finally {
      healthy.restore();
    }
    const good = JSON.parse(healthy.stdout());
    expect(good.bridge).toMatchObject({
      state: "healthy",
      code: "background_verified",
      broker: "running",
    });
    expect(good.summary.state).toBe("ok");
    expect(good.summary.not_checked).not.toContain("bridge");
    expect(runtime.inspectBackgroundHealth).toHaveBeenCalledWith({
      dataDir: expect.any(String),
    });

    runtime.inspectBackgroundHealth.mockResolvedValue({
      state: "degraded",
      code: "background_broker_not_healthy",
      core: "running",
      broker: "stopped",
    });
    const degraded = captureStreams();
    try {
      expect(await run(["status", "--json"], {})).toBe(1);
    } finally {
      degraded.restore();
    }
    const bad = JSON.parse(degraded.stdout());
    expect(bad.bridge).toMatchObject({ state: "degraded", broker: "stopped" });
    expect(bad.summary.state).toBe("partial");
    expect(bad.summary.degraded).toContain("bridge");
    const text = captureStreams();
    try {
      await run(["status"], {});
    } finally {
      text.restore();
    }
    expect(text.stdout()).not.toMatch(/桥接.*(正常|健康)/);
  });

  it("says the bridge was not checked when the inspector cannot answer", async () => {
    runtime.inspectBackgroundHealth.mockRejectedValue(
      new Error("background_data_directory_invalid"),
    );
    const streams = captureStreams();
    try {
      expect(await run(["status", "--json"], {})).toBe(1);
    } finally {
      streams.restore();
    }
    const report = JSON.parse(streams.stdout());
    expect(report.bridge).toMatchObject({
      state: "not_checked",
      code: "bridge_inspection_failed",
    });
    expect(report.summary.not_checked).toContain("bridge");
    expect(report.summary.degraded).not.toContain("bridge");
    expect(report.summary.state).toBe("partial");
  });

  it("asks a stopped runtime for nothing at all", async () => {
    runtime.inspectRuntimeStatus.mockResolvedValue({
      state: "stopped",
      code: "runtime_not_running",
    });
    runtime.inspectBackgroundHealth.mockResolvedValue({
      state: "stopped",
      code: "background_runtime_not_healthy",
      core: "stopped",
      broker: "not_checked",
    });
    const streams = captureStreams();
    try {
      // Inject the launchd probe: a stopped test runtime must not be rewritten
      // to running by a real global `com.tencent.onememory.runtime` label on
      // the host (C8 18:02 BLOCK). The label is per-user, so reading the host's
      // one made this test fail on any machine that had it loaded.
      expect(
        await run(
          ["status", "--json"],
          {},
          {
            platform: "darwin",
            inspectLaunchd: async () => ({ state: "absent" }),
          },
        ),
      ).toBe(3);
    } finally {
      streams.restore();
    }
    const report = JSON.parse(streams.stdout());
    expect(report.runtime.state).toBe("stopped");
    for (const section of Object.values<{ state: string; code: string }>(
      report.sections,
    )) {
      expect(section).toEqual({
        state: "not_checked",
        code: "runtime_not_running",
      });
    }
    expect(transport.get).not.toHaveBeenCalled();
    expect(runtime.discoverRuntimeAttachment).not.toHaveBeenCalled();
  });

  it("reports a service-managed runtime as running from the injected probe", async () => {
    // Complements the stopped case: the injected launchd probe is not a mute —
    // when it says the managed service is running, status must say so, which is
    // what the receipt-based path alone could not do.
    runtime.inspectRuntimeStatus.mockResolvedValue({
      state: "stopped",
      code: "runtime_not_running",
    });
    runtime.inspectBackgroundHealth.mockResolvedValue({
      state: "stopped",
      code: "background_runtime_not_healthy",
      core: "stopped",
      broker: "not_checked",
    });
    transport.get.mockImplementation(async (url: string) => {
      if (url === "/api/v1/runtime/identity") {
        return {
          status: 200,
          data: {
            status: "ok",
            service: "one-memory-core",
            version: "1.2.3",
            instance_id: "instance-1",
          },
        };
      }
      const hit = CORE.get(url);
      return hit ?? { status: 404, data: { detail: "not_found" } };
    });
    const streams = captureStreams();
    try {
      const code = await run(
        ["status", "--json"],
        {},
        {
          platform: "darwin",
          inspectLaunchd: async () => ({ state: "running", pid: 4321 }),
        },
      );
      // Sections may still be unavailable (the process here is synthetic), but
      // the runtime block must reflect the managed service.
      expect(code).not.toBe(3);
    } finally {
      streams.restore();
    }
    const report = JSON.parse(streams.stdout());
    expect(report.runtime).toMatchObject({
      state: "running",
      code: "runtime_service_running",
      instance_id: "service-pid-4321",
    });
  });

  it("separates denied, unavailable and empty instead of reporting success", async () => {
    transport.get.mockImplementation(async (url: string) => {
      if (url === "/api/v1/sources") return { status: 403, data: {} };
      if (url === "/api/v1/model-profiles") return { status: 503, data: {} };
      if (url === "/api/v1/live-sync") return { status: 200, data: [] };
      return CORE.get(url) ?? { status: 404, data: {} };
    });
    const streams = captureStreams();
    try {
      expect(await run(["status", "--json"], {})).toBe(1);
    } finally {
      streams.restore();
    }
    const report = JSON.parse(streams.stdout());
    expect(report.sections.sources).toMatchObject({
      state: "denied",
      code: "http_403",
    });
    expect(report.sections.model).toMatchObject({
      state: "unavailable",
      code: "http_503",
    });
    expect(report.sections.sync).toMatchObject({ state: "ok", sources: 0 });
    expect(report.summary.state).toBe("partial");
    expect(report.summary.degraded).toEqual(["sources", "model"]);
  });

  it("treats a transport failure as unavailable rather than empty", async () => {
    // Structured callers still get the truthful failure envelope and a
    // non-zero exit; the plain-language replacement is only for text mode.
    transport.get.mockRejectedValue(new Error("ECONNREFUSED"));
    const streams = captureStreams();
    try {
      expect(await run(["status", "--json"], {})).toBe(1);
    } finally {
      streams.restore();
    }
    const report = JSON.parse(streams.stdout());
    expect(report.sections.sources).toMatchObject({
      state: "unavailable",
      code: "transport_failed",
    });
  });

  it("keeps a leaked server credential out of the report", async () => {
    for (const argv of [["status", "--json"], ["status"]]) {
      const streams = captureStreams();
      try {
        await run(argv, {});
      } finally {
        streams.restore();
      }
      expect(`${streams.stdout()}${streams.stderr()}`).not.toContain(
        "sk-must-never-be-printed",
      );
      expect(`${streams.stdout()}${streams.stderr()}`).not.toContain(
        "synthetic-runtime-token",
      );
    }
  });
});

describe("SDK user profile", () => {
  it("prints the assembled long-term profile from the v2 endpoint", async () => {
    transport.get.mockImplementation(async (url: string) =>
      url === "/api/v2/user-profile"
        ? {
            status: 200,
            data: {
              request_id: "profile-request",
              profile: {
                memory_id: "l0-head",
                content: "name is Alice",
                layer: "L0_BASIC_INFO",
                basic_info: { name: "Alice" },
              },
              elapsed_ms: 1.2,
            },
          }
        : { status: 404, data: {} },
    );
    const streams = captureStreams();
    try {
      expect(await run(["profile", "--json"], {})).toBe(0);
    } finally {
      streams.restore();
    }
    expect(transport.get).toHaveBeenCalledWith(
      "/api/v2/user-profile",
      undefined,
    );
    expect(JSON.parse(streams.stdout())).toMatchObject({
      schema_version: 2,
      state: "ok",
      profile: { memory_id: "l0-head", content: "name is Alice" },
    });
  });
});

describe("CLI-21 memory reads", () => {
  it("keeps memory reads open long enough for a busy Core and explains failure", async () => {
    const axios = (await import("axios")).default as unknown as {
      create: ReturnType<typeof vi.fn>;
    };
    await run(["status", "--json"], {}).catch(() => undefined);
    const readerConfigs = axios.create.mock.calls.map(
      (call) => call[0] as { timeout?: number },
    );
    // Only the memory list/detail path opts into the 15 second budget; the
    // status/progress reader keeps its existing five second budget.
    expect(
      readerConfigs.filter((config) => config.timeout === 15_000),
    ).toHaveLength(0);

    await run(["memories", "list", "--json"], {}).catch(() => undefined);
    expect(
      axios.create.mock.calls
        .map((call) => call[0] as { timeout?: number })
        .filter((config) => config.timeout === 15_000),
    ).toHaveLength(1);

    transport.get.mockRejectedValue(new Error("ECONNREFUSED"));
    const streams = captureStreams();
    try {
      expect(await run(["memories", "list", "--json"], {})).toBe(1);
    } finally {
      streams.restore();
    }
    expect(JSON.parse(streams.stdout()).page).toEqual({
      state: "unavailable",
      code: "transport_failed",
    });

    const detailStreams = captureStreams();
    try {
      expect(await run(["memories", "show", "m1", "--json"], {})).toBe(1);
    } finally {
      detailStreams.restore();
    }
    expect(JSON.parse(detailStreams.stdout()).memory).toEqual({
      state: "unavailable",
      code: "transport_failed",
    });
  });

  it("lists a page and states truthfully that more remains", async () => {
    transport.get.mockImplementation(async (url: string) => {
      if (url !== "/api/v2/memory-library") return { status: 404, data: {} };
      return {
        status: 200,
        data: {
          total: 12,
          offset: 0,
          limit: 2,
          memories: [memory("m1", "第一条记忆"), memory("m2", "x".repeat(400))],
        },
      };
    });
    const streams = captureStreams();
    try {
      expect(
        await run(["memories", "list", "--limit", "2", "--json"], {}),
      ).toBe(0);
    } finally {
      streams.restore();
    }
    const report = JSON.parse(streams.stdout());
    expect(report.page).toMatchObject({
      state: "ok",
      total: 12,
      offset: 0,
      limit: 2,
      returned: 2,
      has_more: true,
    });
    expect(report.memories[0]).toMatchObject({
      memory_id: "m1",
      status: "active",
      layer: "L2_FACT",
      content_truncated: false,
    });
    expect(report.memories[1]).toMatchObject({
      content_truncated: true,
      content_characters: 400,
    });
    expect(report.memories[1].content.length).toBeLessThan(400);
  });

  it("passes the requested filters through as query parameters", async () => {
    await run(
      [
        "memories",
        "list",
        "--status",
        "active",
        "--offset",
        "10",
        "--limit",
        "5",
        "--json",
      ],
      {},
    ).catch(() => undefined);
    expect(transport.get).toHaveBeenCalledWith("/api/v2/memory-library", {
      params: {
        statuses: "active",
        offset: 10,
        limit: 5,
      },
    });
  });

  it("rejects retired memory query flags before calling Core", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "om-cli-query-"));
    const file = path.join(directory, "query.txt");
    await writeFile(file, "机密查询\n", { mode: 0o600 });
    expect(
      await run(["memories", "list", "--query-file", file, "--json"], {}),
    ).toBe(2);
    expect(transport.get).not.toHaveBeenCalled();
  });

  it("rejects contradictory or unreadable query input before calling Core", async () => {
    for (const argv of [
      ["memories", "list", "--query", "a", "--query-file", "/tmp/none"],
      ["memories", "list", "--query-file", "/tmp/definitely-absent-om-query"],
      ["memories", "list", "--limit", "0"],
      ["memories", "list", "--limit", "501"],
      ["memories", "list", "--offset", "-1"],
      ["memories", "list", "--status", ""],
      ["memories", "show"],
      ["memories"],
    ]) {
      const streams = captureStreams();
      try {
        expect(await run(argv, {})).toBe(2);
      } finally {
        streams.restore();
      }
      expect(streams.stdout()).toBe("");
    }
    expect(transport.get).not.toHaveBeenCalled();
  });

  it("distinguishes an empty page from a denied or missing one", async () => {
    transport.get.mockResolvedValue({
      status: 200,
      data: { total: 0, offset: 0, limit: 100, memories: [] },
    });
    const empty = captureStreams();
    try {
      expect(await run(["memories", "list", "--json"], {})).toBe(0);
    } finally {
      empty.restore();
    }
    expect(JSON.parse(empty.stdout()).page).toMatchObject({
      state: "ok",
      total: 0,
      returned: 0,
      has_more: false,
    });

    transport.get.mockResolvedValue({ status: 403, data: {} });
    const denied = captureStreams();
    try {
      expect(await run(["memories", "list", "--json"], {})).toBe(1);
    } finally {
      denied.restore();
    }
    expect(JSON.parse(denied.stdout()).page).toMatchObject({
      state: "denied",
      code: "http_403",
    });

    transport.get.mockResolvedValue({ status: 404, data: {} });
    const missing = captureStreams();
    try {
      expect(await run(["memories", "show", "m9", "--json"], {})).toBe(1);
    } finally {
      missing.restore();
    }
    expect(JSON.parse(missing.stdout()).memory).toMatchObject({
      state: "not_found",
      code: "http_404",
    });
  });

  it("shows one SDK memory without fabricated Evidence or versions", async () => {
    transport.get.mockResolvedValue({
      status: 200,
      data: memory("m1", "记忆正文"),
    });
    const streams = captureStreams();
    try {
      expect(await run(["memories", "show", "m1", "--json"], {})).toBe(0);
    } finally {
      streams.restore();
    }
    const report = JSON.parse(streams.stdout());
    expect(transport.get).toHaveBeenCalledWith(
      "/api/v2/memories/m1",
      undefined,
    );
    expect(report.memory).toMatchObject({
      state: "ok",
      memory_id: "m1",
      layer: "L2_FACT",
      status: "active",
    });
    expect(report.evidence).toEqual([]);
    expect(report.history).toEqual([]);
  });

  // Core registers /api/v1/memories/graph before /{memory_id}, and that static
  // handler initializes the HY bridge. A memory id that collides with a
  // reserved segment must never become a request.
  it("refuses a memory id that collides with a static Core route", async () => {
    for (const id of ["graph", "ask"]) {
      const streams = captureStreams();
      try {
        expect(await run(["memories", "show", id, "--json"], {})).toBe(2);
      } finally {
        streams.restore();
      }
      expect(streams.stdout()).toBe("");
    }
    expect(transport.get).not.toHaveBeenCalled();
    expect(isReadOnlyPath("/api/v1/memories/graph")).toBe(false);
    expect(isReadOnlyPath("/api/v1/memories/ask")).toBe(false);
    expect(isReadOnlyPath("/api/v1/memories/m1")).toBe(true);
    expect(isReadOnlyPath("/api/v2/memory-library")).toBe(true);
    expect(isReadOnlyPath("/api/v2/memories/m1")).toBe(true);
    expect(isReadOnlyPath("/api/v1/model-content-permission")).toBe(false);
    expect(
      isReadOnlyPath(
        "/api/v1/memory-mutations/123e4567-e89b-12d3-a456-426614174000",
      ),
    ).toBe(true);
    expect(isReadOnlyPath("/api/v1/memory-mutations/../sources")).toBe(false);
  });

  it("refuses a memory id that would escape the memories path", async () => {
    for (const id of ["../sources", "m1/scopes", "m 1", ""]) {
      const streams = captureStreams();
      try {
        expect(await run(["memories", "show", id, "--json"], {})).toBe(2);
      } finally {
        streams.restore();
      }
    }
    expect(transport.get).not.toHaveBeenCalled();
  });
});

// Main's READ-REVIEW (2026-09-19) drove these against the real reader: a 200
// was cast to the expected type, so a wrong record, an unavailable excerpt, an
// oversized page and a non-string model field all reached stdout as success.
describe("malformed success responses", () => {
  it("refuses a detail that is not the memory that was asked for", async () => {
    transport.get.mockResolvedValue({
      status: 200,
      data: {
        memory: memory("wrong-id", "SYNTHETIC_WRONG_RECORD_BODY"),
        evidence: [],
        history: [],
      },
    });
    const streams = captureStreams();
    try {
      expect(
        await run(["memories", "show", "requested-id", "--json"], {}),
      ).toBe(1);
    } finally {
      streams.restore();
    }
    expect(streams.stdout()).not.toContain("SYNTHETIC_WRONG_RECORD_BODY");
    expect(JSON.parse(streams.stdout()).memory).toMatchObject({
      state: "invalid",
      code: "response_invalid",
    });
  });

  it("never prints an excerpt for evidence marked unavailable", async () => {
    transport.get.mockResolvedValue({
      status: 200,
      data: {
        memory: memory("m1", "正文"),
        evidence: [
          {
            evidence_id: "e1",
            available: false,
            verbatim: "SYNTHETIC_UNAVAILABLE_BODY",
          },
        ],
        history: [],
      },
    });
    for (const argv of [
      ["memories", "show", "m1", "--json"],
      ["memories", "show", "m1"],
    ]) {
      const streams = captureStreams();
      try {
        await run(argv, {});
      } finally {
        streams.restore();
      }
      expect(`${streams.stdout()}${streams.stderr()}`).not.toContain(
        "SYNTHETIC_UNAVAILABLE_BODY",
      );
    }
  });

  it("refuses evidence that does not belong to the returned memory", async () => {
    transport.get.mockResolvedValue({
      status: 200,
      data: {
        memory: memory("m1", "正文"),
        evidence: [
          {
            evidence_id: "not-cited",
            available: true,
            verbatim: "SYNTHETIC_FOREIGN_BODY",
          },
        ],
        history: [],
      },
    });
    const streams = captureStreams();
    try {
      expect(await run(["memories", "show", "m1", "--json"], {})).toBe(1);
    } finally {
      streams.restore();
    }
    expect(streams.stdout()).not.toContain("SYNTHETIC_FOREIGN_BODY");
    expect(JSON.parse(streams.stdout()).memory).toMatchObject({
      state: "invalid",
    });
  });

  it("refuses a page whose bounds contradict the request", async () => {
    transport.get.mockResolvedValue({
      status: 200,
      data: {
        total: 1,
        offset: 99,
        limit: 500,
        items: [memory("m1", "SYNTHETIC_WRONG_PAGE")],
      },
    });
    const streams = captureStreams();
    try {
      expect(
        await run(["memories", "list", "--limit", "1", "--json"], {}),
      ).toBe(1);
    } finally {
      streams.restore();
    }
    expect(streams.stdout()).not.toContain("SYNTHETIC_WRONG_PAGE");
    const report = JSON.parse(streams.stdout());
    expect(report.page).toMatchObject({
      state: "invalid",
      code: "response_invalid",
    });
    expect(report.memories).toEqual([]);
  });

  it("refuses more page items than the requested output budget", async () => {
    transport.get.mockResolvedValue({
      status: 200,
      data: {
        total: 2,
        offset: 0,
        limit: 1,
        items: Array.from({ length: 2 }, (_, index) =>
          memory(`m${index}`, "SYNTHETIC_OVERSIZED_PAGE"),
        ),
      },
    });
    const streams = captureStreams();
    try {
      expect(
        await run(["memories", "list", "--limit", "1", "--json"], {}),
      ).toBe(1);
    } finally {
      streams.restore();
    }
    expect(streams.stdout()).not.toContain("SYNTHETIC_OVERSIZED_PAGE");
    expect(JSON.parse(streams.stdout()).page).toMatchObject({
      state: "invalid",
      code: "response_invalid",
    });
  });

  it("refuses a model profile whose string fields are not strings", async () => {
    transport.get.mockImplementation(async (url: string) => {
      if (url !== "/api/v1/model-profiles") return CORE.get(url);
      return {
        status: 200,
        data: [
          {
            profile_id: "p1",
            name: { api_key: "SYNTHETIC_NESTED_SECRET" },
            provider_kind: "openai_compatible",
            model: "synthetic-model",
            has_api_key: true,
            is_active: true,
          },
        ],
      };
    });
    const streams = captureStreams();
    try {
      expect(await run(["status", "--json"], {})).toBe(1);
    } finally {
      streams.restore();
    }
    expect(streams.stdout()).not.toContain("SYNTHETIC_NESTED_SECRET");
    expect(JSON.parse(streams.stdout()).sections.model).toMatchObject({
      state: "invalid",
      code: "response_invalid",
    });
  });

  it("bounds how much of a response it will buffer at all", async () => {
    const axios = (await import("axios")).default as unknown as {
      create: ReturnType<typeof vi.fn>;
    };
    await run(["status", "--json"], {});
    const [config] = axios.create.mock.calls[0] as [
      { maxContentLength?: number },
    ];
    expect(config.maxContentLength).toBeGreaterThan(0);
    expect(config.maxContentLength).toBeLessThanOrEqual(8 * 1024 * 1024);
  });
});

// A target Core refuses (outside the data dir) is a security rejection, not a
// usage error: the flags were well-formed. Folding it into `usage_error`/exit 2
// told a script "you typed it wrong". Runtime answers 422 for this case.
describe("backup create target refusal is rejected, not usage_error", () => {
  it("maps a 422 to rejected/exit 1 with the machine code", async () => {
    transport.post.mockImplementation(async (url: string) =>
      String(url).endsWith("/api/v1/backup")
        ? { status: 422, data: { detail: "backup_target_outside_data_dir" } }
        : { status: 404, data: {} },
    );
    const streams = captureStreams();
    let code: number;
    try {
      code = await run(
        ["backup", "create", "--target", "/tmp/outside.db", "--yes", "--json"],
        {},
      );
    } finally {
      streams.restore();
    }
    expect(code!).toBe(1);
    const document = JSON.parse(streams.stderr());
    expect(document.status).not.toBe("usage_error");
    expect(document).toMatchObject({
      command: "backup",
      verb: "create",
      state: "rejected",
      code: "backup_target_outside_data_dir",
      exit_code: 1,
    });
  });
});

describe("read-only discipline", () => {
  it("only ever issues GET, and only to the read-only allowlist", async () => {
    await run(["status", "--json"], {});
    await run(["memories", "list", "--json"], {}).catch(() => undefined);
    await run(["memories", "show", "m1", "--json"], {}).catch(() => undefined);
    expect(transport.get.mock.calls.length).toBeGreaterThan(0);
    for (const [url] of transport.get.mock.calls as [string][]) {
      expect(READ_ONLY_PATHS.some((allowed) => url.startsWith(allowed))).toBe(
        true,
      );
    }
    for (const method of [
      transport.post,
      transport.put,
      transport.patch,
      transport.delete,
      transport.request,
    ]) {
      expect(method).not.toHaveBeenCalled();
    }
  });

  it("stays away from legacy recall and detect, which are not side-effect free", async () => {
    await run(["status", "--json"], {});
    await run(["memories", "list", "--json"], {}).catch(() => undefined);
    const requested = (transport.get.mock.calls as [string][]).map(
      ([url]) => url,
    );
    for (const forbidden of [
      "/api/v1/recall",
      "/api/v1/context/search",
      "/api/v1/conversations/search",
      "/api/v1/memories/graph",
    ]) {
      expect(requested).not.toContain(forbidden);
    }
    expect(requested.some((url) => url.includes("/detect"))).toBe(false);
  });

  it("authenticates with the verified runtime token and no proxy", async () => {
    const axios = (await import("axios")).default as unknown as {
      create: ReturnType<typeof vi.fn>;
    };
    await run(["status", "--json"], {});
    expect(axios.create).toHaveBeenCalledWith(
      expect.objectContaining({
        baseURL: "http://127.0.0.1:8765",
        headers: { Authorization: "Bearer synthetic-runtime-token" },
        proxy: false,
        maxRedirects: 0,
      }),
    );
  });
});

function memory(id: string, content: string) {
  return {
    memory_id: id,
    user_id: "local",
    agent_id: "one-memory",
    layer: "L2_FACT",
    status: "active",
    is_latest: true,
    supersedes: [],
    superseded_by: [],
    content,
    tags: [],
    owner: "user",
    source_raw_ids: ["raw-1"],
    session_id: "s1",
    source: "codex",
    memory_at: null,
    gmt_created: null,
  };
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
