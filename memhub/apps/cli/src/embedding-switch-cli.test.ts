import { beforeEach, describe, expect, it, vi } from "vitest";

import { run } from "./cli.js";
import { renderContextText } from "./context-search.js";
import { describeEmbeddingSwitchSummary } from "./embedding-switch-flow.js";
import { composeStatusReport, renderStatusText } from "./status-report.js";

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
}));
vi.mock("@one-memory/runtime-control", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@one-memory/runtime-control")>()),
  ...runtime,
}));

const PROFILE = "44444444-4444-4444-8444-444444444444";
const JOB = "55555555-5555-4555-8555-555555555555";
const SPACE = "66666666-6666-4666-8666-666666666666";

function plan(overrides: Record<string, unknown> = {}) {
  return {
    needed: true,
    reason: "embedding_identity_changed",
    plan_token: "token-1",
    source: {
      space_id: "77777777-7777-4777-8777-777777777777",
      model: "bge-small-zh-v1.5",
      dims: 512,
      collection_base: "memhub_aaaaaaaaaaaaaaaa",
      vectors: 12480,
    },
    target: {
      space_id: null,
      model: "text-embedding-v3",
      dims: 1024,
      collection_base: "",
    },
    target_profile_id: PROFILE,
    target_revision: 5,
    target_provider_kind: "openai_compatible",
    target_local: false,
    sources: [],
    total_vectors: 12480,
    users: 2,
    graph_vectors: null,
    estimate_s: 120,
    requests: 195,
    disk_bytes_needed: 70 * 1024 * 1024,
    disk_bytes_free: 10 * 1024 * 1024 * 1024,
    retention_days: 14,
    blockers: [],
    ...overrides,
  };
}

function job(
  state: string,
  percent: number,
  extra: Record<string, unknown> = {},
) {
  return {
    job_id: JOB,
    kind: "switch",
    state,
    source: plan().source,
    target: { ...plan().target, space_id: SPACE },
    stray_space_ids: [],
    target_profile_id: PROFILE,
    target_revision: 5,
    requested_by: "cli",
    progress: {
      phase: state === "reembedding" ? "vectors" : "prepare",
      done: Math.round((12480 * percent) / 100),
      total: 12480,
      percent,
      eta_s: 60,
    },
    cancel_requested: false,
    error_code: null,
    attempt: 1,
    created_at: "T1",
    updated_at: "T1",
    finished_at: state === "done" ? "T2" : null,
    ...extra,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  for (const method of Object.values(transport)) method.mockReset();
  runtime.inspectRuntimeStatus.mockResolvedValue({
    state: "running",
    code: "runtime_verified",
    instanceId: "instance-1",
    host: "127.0.0.1",
    port: 8765,
    version: "1.2.3",
  });
  runtime.discoverRuntimeAttachment.mockResolvedValue({
    instanceId: "instance-1",
    apiToken: "synthetic-runtime-token",
    host: "127.0.0.1",
    port: 8765,
    servicePid: 4242,
    health: {
      status: "ok",
      service: "one-memory-core",
      version: "1.2.3",
      instance_id: "instance-1",
    },
  });
});

describe("models activate with an embedding change", () => {
  it("prints the plan and exits 2 without --yes on a non-terminal", async () => {
    transport.post.mockImplementation(async (url: string) =>
      url === "/api/v1/embedding-switches/plan"
        ? { status: 200, data: plan() }
        : { status: 404, data: {} },
    );
    const result = await capturedRun(["models", "activate", PROFILE]);
    expect(result.code).toBe(2);
    expect(result.stdout).toContain("将更换向量模型");
    expect(result.stdout).toContain("bge-small-zh-v1.5（512 维）");
    expect(result.stdout).toContain("text-embedding-v3（1024 维）");
    expect(result.stdout).toContain("12,480");
    expect(result.stdout).toContain("重新运行并加上 --yes 以确认");
    expect(transport.post.mock.calls.map(([url]) => url)).toEqual([
      "/api/v1/embedding-switches/plan",
    ]);
  });

  it("starts the switch with --yes and emits JSON progress lines", async () => {
    transport.post.mockImplementation(async (url: string) => {
      if (url === "/api/v1/embedding-switches/plan") {
        return { status: 200, data: plan() };
      }
      if (url === "/api/v1/embedding-switches") {
        return { status: 202, data: job("preparing", 0) };
      }
      return { status: 404, data: {} };
    });
    const answers = [
      {
        phase: "running",
        writes_paused: true,
        job: job("reembedding", 50),
        last_finished: null,
      },
      {
        phase: "idle",
        writes_paused: false,
        job: null,
        last_finished: job("done", 100),
      },
    ];
    transport.get.mockImplementation(async (url: string) =>
      url === "/api/v1/embedding-switches/current"
        ? { status: 200, data: { schema_version: 1, ...answers.shift() } }
        : { status: 404, data: {} },
    );
    const result = await capturedRun(
      ["models", "activate", PROFILE, "--yes", "--wait", "--json"],
      { watchWait: async () => undefined },
    );
    expect(result.code).toBe(0);
    const lines = result.stdout
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line));
    expect(lines.map((line) => line.state)).toEqual([
      "preparing",
      "reembedding",
      "done",
      "done",
    ]);
    expect(lines[1]).toMatchObject({ percent: 50, job_id: JOB });
    expect(lines.at(-1)).toMatchObject({ result: "switched" });
    expect(transport.post).toHaveBeenCalledWith(
      "/api/v1/embedding-switches",
      {
        profile_id: PROFILE,
        expected_revision: 5,
        plan_token: "token-1",
        requested_by: "cli",
      },
      undefined,
    );
  });

  it("refuses a plan with blockers and starts nothing", async () => {
    transport.post.mockResolvedValue({
      status: 200,
      data: plan({ blockers: ["sdk_reembed_unavailable"] }),
    });
    const result = await capturedRun([
      "models",
      "activate",
      PROFILE,
      "--yes",
      "--json",
    ]);
    expect(result.code).toBe(1);
    expect(JSON.parse(result.stdout)).toMatchObject({
      state: "blocked",
      code: "sdk_reembed_unavailable",
    });
    expect(transport.post).toHaveBeenCalledTimes(1);
  });

  it("says nothing is re-embedded when only generation changed", async () => {
    transport.post.mockImplementation(async (url: string) =>
      url === "/api/v1/embedding-switches/plan"
        ? {
            status: 200,
            data: plan({ needed: false, reason: "same_embedding" }),
          }
        : { status: 200, data: profileResponse() },
    );
    const result = await capturedRun(["models", "activate", PROFILE]);
    expect(result.code).toBe(0);
    expect(result.stdout).toContain("向量模型未变化，无需重新生成向量。");
  });

  it("reads the plan carried by a 409 embedding_switch_required", async () => {
    transport.post.mockImplementation(async (url: string) =>
      url.endsWith("/activate")
        ? {
            status: 409,
            data: { detail: "embedding_switch_required", plan: plan() },
          }
        : { status: 404, data: { detail: "Not Found" } },
    );
    const result = await capturedRun(["models", "activate", PROFILE, "--json"]);
    expect(result.code).toBe(2);
    expect(JSON.parse(result.stdout)).toMatchObject({
      state: "confirmation_required",
      code: "embedding_switch_required",
    });
  });

  it("does not treat a domain 404 as an old-Core route miss", async () => {
    transport.post.mockResolvedValue({
      status: 404,
      data: { detail: "embedding_switch_not_found" },
    });
    const result = await capturedRun(["models", "activate", PROFILE, "--json"]);
    expect(result.code).toBe(1);
    expect(
      transport.post.mock.calls.some(([url]) =>
        String(url).endsWith("/activate"),
      ),
    ).toBe(false);
  });
});

describe("models switch and spaces", () => {
  it("reports no switch, then cancels the running one", async () => {
    transport.get.mockResolvedValueOnce({
      status: 200,
      data: {
        schema_version: 1,
        phase: "idle",
        writes_paused: false,
        job: null,
        last_finished: null,
      },
    });
    const idle = await capturedRun(["models", "switch", "status"]);
    expect(idle.code).toBe(0);
    expect(idle.stdout).toContain("没有进行中的向量模型切换");

    transport.get.mockResolvedValueOnce({
      status: 200,
      data: {
        schema_version: 1,
        phase: "running",
        writes_paused: true,
        job: job("reembedding", 20),
        last_finished: null,
      },
    });
    transport.post.mockResolvedValue({
      status: 200,
      data: job("reembedding", 20, { cancel_requested: true }),
    });
    const cancelled = await capturedRun([
      "models",
      "switch",
      "cancel",
      "--json",
    ]);
    expect(cancelled.code).toBe(0);
    expect(transport.post).toHaveBeenCalledWith(
      `/api/v1/embedding-switches/${JOB}/cancel`,
      {},
      undefined,
    );
    expect(JSON.parse(cancelled.stdout)).toMatchObject({
      cancel_requested: true,
    });
  });

  it("lists spaces and needs --yes before cleaning one up", async () => {
    transport.get.mockResolvedValue({
      status: 200,
      data: {
        schema_version: 1,
        live_space_id: "x",
        spaces: [
          {
            space_id: SPACE,
            state: "retired",
            model: "bge-small-zh-v1.5",
            dims: 512,
            vectors: 10,
            keep_until: "2026-11-15T00:00:00+00:00",
          },
          {
            space_id: "88888888-8888-4888-8888-888888888888",
            state: "dropped",
            model: "m",
            dims: 8,
          },
        ],
        scanned: [],
        scan_error: null,
      },
    });
    const listed = await capturedRun(["models", "spaces"]);
    expect(listed.code).toBe(0);
    expect(listed.stdout).toContain("已停用");
    expect(listed.stdout).toContain("保留到 2026-11-15");
    expect(listed.stdout).not.toContain("88888888");

    const preview = await capturedRun(["models", "spaces", "--cleanup", SPACE]);
    expect(preview.code).toBe(0);
    expect(preview.stdout).toContain("--yes");
    expect(transport.delete).not.toHaveBeenCalled();

    transport.delete.mockResolvedValue({
      status: 200,
      data: { schema_version: 1 },
    });
    const dropped = await capturedRun([
      "models",
      "spaces",
      "--cleanup",
      SPACE,
      "--yes",
    ]);
    expect(dropped.code).toBe(0);
    expect(transport.delete).toHaveBeenCalledWith(
      `/api/v1/embedding-spaces/${SPACE}`,
      undefined,
    );
  });
});

describe("status and search wording", () => {
  it("shows the switch line in memhub status", async () => {
    const reader = async (path: string) =>
      path === "/api/v1/memory-processing"
        ? {
            state: "ok" as const,
            data: {
              pending: 3,
              fresh_pending: 1,
              backlog_pending: 2,
              outside_window_pending: 0,
              running: 0,
              waiting_for_model: 0,
              completed: 9,
              failed: 0,
              embedding_switch: {
                job_id: JOB,
                kind: "switch",
                state: "reembedding",
                percent: 50,
                eta_s: 60,
              },
            },
          }
        : { state: "unavailable" as const, code: "http_503" };
    const report = await composeStatusReport({
      dataDir: "/synthetic/data",
      runtime: {
        state: "running",
        code: "runtime_verified",
        instanceId: "instance-1",
        host: "127.0.0.1",
        port: 8765,
      } as never,
      background: null,
      reader: reader as never,
    });
    const text = renderStatusText(report, "MemHub");
    expect(text).toContain("向量模型：切换中 50%，记忆整理已暂停");
    expect(
      describeEmbeddingSwitchSummary({
        kind: "merge",
        state: "reembedding",
        percent: 30,
      }),
    ).toBe("正在合并旧向量数据 30%，记忆整理已暂停");
    expect(
      describeEmbeddingSwitchSummary({
        kind: "merge",
        state: "scanning",
        percent: 0,
      }),
    ).toBe("正在检查向量数据…");
  });

  it("names an unavailable embedding service instead of 'try later'", () => {
    expect(
      renderContextText({
        schema_version: 2,
        product: "memhub",
        state: "unavailable",
        code: "embedding_unavailable",
        items: [],
      }),
    ).toContain("向量服务不可用");
  });
});

function profileResponse() {
  return {
    profile_id: PROFILE,
    name: "Synthetic",
    provider_kind: "openai_compatible",
    base_url: "https://example.invalid/v1",
    model: "synthetic-model-2",
    embedding_model: "text-embedding-v3",
    embedding_provider_kind: "openai_compatible",
    embedding_base_url: "https://example.invalid/v1",
    embedding_dimensions: 1024,
    has_api_key: true,
    credential_state: "available",
    timeout_seconds: 30,
    max_concurrency: 1,
    context_window: null,
    disable_thinking: true,
    capabilities: {
      endpoint_reachable: true,
      model_available: true,
      structured_output: true,
      embedding_available: true,
    },
    revision: 5,
    is_active: true,
    created_at: "2026-09-19T00:00:00Z",
    updated_at: "2026-09-19T00:00:00Z",
  };
}

async function capturedRun(
  arguments_: string[],
  controls: Parameters<typeof run>[2] = {},
) {
  let stdout = "";
  let stderr = "";
  const stdoutSpy = vi
    .spyOn(process.stdout, "write")
    .mockImplementation((chunk) => {
      stdout += String(chunk);
      return true;
    });
  const stderrSpy = vi
    .spyOn(process.stderr, "write")
    .mockImplementation((chunk) => {
      stderr += String(chunk);
      return true;
    });
  try {
    return {
      code: await run(
        arguments_,
        { ONE_MEMORY_DATA_DIR: "/synthetic/data" },
        { stdinIsTTY: false, ...controls },
      ),
      stdout,
      stderr,
    };
  } finally {
    stdoutSpy.mockRestore();
    stderrSpy.mockRestore();
  }
}
