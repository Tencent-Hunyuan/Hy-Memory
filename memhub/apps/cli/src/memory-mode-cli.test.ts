import { beforeEach, describe, expect, it, vi } from "vitest";

import { run } from "./cli.js";
import { modeEffects, parseSettingsCommand } from "./memory-mode-flow.js";
import { composeStatusReport, renderStatusText } from "./status-report.js";

const transport = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
}));
vi.mock("axios", () => ({
  default: {
    create: vi.fn(() => transport),
    get: vi.fn(),
    post: vi.fn(),
    isAxiosError: () => false,
  },
}));

const runtime = vi.hoisted(() => ({
  inspectRuntimeStatus: vi.fn(),
  discoverRuntimeAttachment: vi.fn(),
}));
vi.mock("@one-memory/runtime-control", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@one-memory/runtime-control")>()),
  ...runtime,
}));

const RUN_ID = "11111111-2222-4333-8444-555555555555";

function modeState(mode: string, configured = mode) {
  return {
    mode,
    configured_mode: configured,
    forced_by_env: false,
    modes: ["lite", "pro", "ultra"],
    graph_build: { state: "idle", users: {} },
  };
}

function receipt(state: string, extra: Record<string, unknown> = {}) {
  return {
    run_id: RUN_ID,
    user_id: "wife",
    agent_id: "all-apps",
    state,
    trigger: "manual",
    started_at: "2026-11-01T03:00:00Z",
    rounds: state === "running" ? 0 : 2,
    tasks_processed: state === "running" ? 0 : 5,
    batch_failures: 0,
    capped: false,
    elapsed_ms: 10,
    results: { schemas: ["never printed"] },
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
  transport.get.mockImplementation(async (url: string) =>
    url === "/api/v1/settings/memory-mode"
      ? { status: 200, data: modeState("pro") }
      : { status: 404, data: {} },
  );
});

describe("memhub settings mode", () => {
  it("shows the current mode without changing it", async () => {
    const result = await capturedRun(["settings", "mode", "--json"]);
    expect(result.code).toBe(0);
    expect(JSON.parse(result.stdout).mode.mode).toBe("pro");
    expect(transport.put).not.toHaveBeenCalled();
  });

  it("prints the plan and changes nothing without --yes", async () => {
    const result = await capturedRun(["settings", "mode", "lite"]);
    expect(result.code).toBe(0);
    expect(transport.put).not.toHaveBeenCalled();
    expect(result.stdout).toContain("更高模式生成的记忆仍保存着");
    expect(result.stdout).toContain("memhub settings mode lite --yes");
    const json = JSON.parse(
      (await capturedRun(["settings", "mode", "ultra", "--json"])).stdout,
    );
    expect(json.state).toBe("preview");
    expect(json.applied).toBe(false);
    expect(json.effects).toEqual(["existing_memories_added_to_graph"]);
  });

  it("changes the mode with --yes and reports what changed", async () => {
    transport.put.mockResolvedValue({
      status: 200,
      data: {
        ...modeState("lite"),
        previous_mode: "pro",
        effects: ["higher_mode_memories_hidden"],
      },
    });
    const result = await capturedRun(["settings", "mode", "lite", "--yes"]);
    expect(result.code).toBe(0);
    expect(transport.put).toHaveBeenCalledWith(
      "/api/v1/settings/memory-mode",
      { mode: "lite" },
      undefined,
    );
    expect(result.stdout).toContain("已切换");
    expect(result.stdout).toContain("原始对话：全部保留");
  });

  it("does nothing for the mode already set, and refuses bad input", async () => {
    const same = await capturedRun(["settings", "mode", "pro", "--yes"]);
    expect(same.code).toBe(0);
    expect(same.stdout).toContain("已经是这个模式");
    expect(transport.put).not.toHaveBeenCalled();
    for (const bad of [
      ["settings"],
      ["settings", "mode", "max"],
      ["settings", "mode", "--yes"],
      ["settings", "mode", "lite", "pro"],
      ["settings", "theme", "dark"],
    ]) {
      expect((await capturedRun(bad)).code, bad.join(" ")).toBe(2);
    }
  });

  it("plans the same effects Core reports", () => {
    expect(modeEffects("lite", "pro")).toEqual([
      "earlier_raw_not_extracted",
      "lite_search_stops",
    ]);
    expect(modeEffects("ultra", "lite")).toEqual([
      "higher_mode_memories_hidden",
      "evolve_stopped",
    ]);
    expect(modeEffects("pro", "pro")).toEqual([]);
    expect(parseSettingsCommand(["mode", "ultra", "--yes"])).toEqual({
      kind: "set",
      mode: "ultra",
      confirmed: true,
    });
  });
});

describe("memhub evolve", () => {
  it("starts a run for the named user and returns at once", async () => {
    transport.post.mockResolvedValue({ status: 202, data: receipt("running") });
    const result = await capturedRun([
      "evolve",
      "--user-id",
      "wife",
      "--agent-id",
      "all-apps",
      "--max-batches",
      "3",
      "--json",
    ]);
    expect(result.code).toBe(0);
    expect(transport.post).toHaveBeenCalledWith(
      "/api/v2/evolve",
      { user_id: "wife", agent_id: "all-apps", max_batches: 3 },
      undefined,
    );
    const document = JSON.parse(result.stdout);
    expect(document.state).toBe("running");
    expect(document.run.results).toBeUndefined();
  });

  it("follows the run with --wait until it ends", async () => {
    transport.post.mockResolvedValue({ status: 202, data: receipt("running") });
    const reads = [receipt("running"), receipt("succeeded")];
    transport.get.mockImplementation(async (url: string) =>
      url === `/api/v2/evolve/${RUN_ID}`
        ? { status: 200, data: reads.shift() }
        : { status: 404, data: {} },
    );
    const result = await capturedRun(["evolve", "--wait"], {
      sleep: async () => undefined,
    });
    expect(result.code).toBe(0);
    expect(transport.post).toHaveBeenCalledWith(
      "/api/v2/evolve",
      {},
      undefined,
    );
    expect(result.stdout).toContain("进化完成");
    expect(result.stdout).toContain("2 轮");
  });

  it("names the mode when Core refuses outside ultra", async () => {
    transport.post.mockResolvedValue({
      status: 409,
      data: { detail: "memory_mode_not_ultra" },
    });
    const result = await capturedRun(["evolve"]);
    expect(result.code).toBe(1);
    expect(result.stdout).toContain("memhub settings mode ultra --yes");
    expect((await capturedRun(["evolve", "--max-batches", "0"])).code).toBe(2);
    expect((await capturedRun(["evolve", "--now"])).code).toBe(2);
  });
});

describe("status pipeline", () => {
  it("reports pipeline.mode and the last evolve from memory-processing", async () => {
    const processing = {
      pending: 0,
      fresh_pending: 0,
      backlog_pending: 0,
      outside_window_pending: 0,
      running: 0,
      waiting_for_model: 0,
      completed: 3,
      failed: 0,
      blocked_stages: [],
      pipeline: {
        mode: "ultra",
        configured_mode: "ultra",
        forced_by_env: false,
        last_evolve_at: "2026-11-01T03:00:05+08:00",
        graph_build: { state: "succeeded", users: {} },
        evolve: {
          mode: "ultra",
          schedule: { enabled: true, time: "03:00" },
          running: [],
          users: {
            local: {
              last_run_at: "2026-11-01T03:00:05+08:00",
              last_result: { state: "succeeded", tasks_processed: 4 },
              last_error: null,
            },
          },
        },
      },
    };
    const report = await composeStatusReport({
      dataDir: "/tmp/isolated",
      runtime: { state: "running", code: "ok", host: "127.0.0.1", port: 1 },
      background: null,
      reader: (async (path: string) =>
        path === "/api/v1/memory-processing"
          ? { state: "ok", data: processing }
          : { state: "unavailable", code: "x" }) as never,
    });
    expect(report.pipeline?.mode).toBe("ultra");
    expect(report.pipeline?.last_evolve_at).toBe("2026-11-01T03:00:05+08:00");
    expect(report.pipeline?.evolve_users.local?.state).toBe("succeeded");
    const text = renderStatusText(report, "MemHub");
    expect(text).toContain("记忆模式：ultra");
    expect(text).toContain("每日 03:00 自动进化");
  });
});

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
        controls,
      ),
      stdout,
      stderr,
    };
  } finally {
    stdoutSpy.mockRestore();
    stderrSpy.mockRestore();
  }
}
