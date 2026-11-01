import { beforeEach, describe, expect, it, vi } from "vitest";

import { run } from "./cli.js";
import { parseFeaturesCommand } from "./memory-features-flow.js";
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

const DEFAULTS: Record<string, boolean> = {
  graph_rerank: false,
  same_series_timeline: false,
  episode_recall: false,
  session_episodes: false,
  episode_summary: false,
  intention: true,
  few_shot: false,
};

function featuresState(
  configured: Record<string, boolean> = {},
  mode = "pro",
  extra: Record<string, unknown> = {},
) {
  const nonDefault: Record<string, boolean> = {};
  const features = Object.entries(DEFAULTS).map(([name, fallback]) => {
    const enabled = configured[name] ?? fallback;
    if (enabled !== fallback) nonDefault[name] = enabled;
    const needsUltra = name === "graph_rerank";
    const reason = !enabled
      ? "disabled"
      : needsUltra && mode !== "ultra"
        ? "requires_ultra"
        : null;
    return {
      name,
      enabled,
      configured: name in configured ? configured[name] : null,
      default: fallback,
      active: reason === null,
      reason,
      side: "read",
      requires_mode: needsUltra ? "ultra" : "pro",
      requires_feature: null,
      cost: "none",
      sdk_field: `recall.${name}`,
      env: `MEMORY_${name.toUpperCase()}`,
      description: name,
    };
  });
  return { mode, features, non_default: nonDefault, ...extra };
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
    url === "/api/v1/settings/memory-features"
      ? { status: 200, data: featuresState() }
      : { status: 404, data: {} },
  );
});

describe("memhub settings features", () => {
  it("lists every feature with its value and whether it acts", async () => {
    const result = await capturedRun(["settings", "features", "--json"]);
    expect(result.code).toBe(0);
    const json = JSON.parse(result.stdout);
    expect(json.state).toBe("ok");
    expect(json.features.features).toHaveLength(7);
    expect(transport.put).not.toHaveBeenCalled();
    const text = (await capturedRun(["settings", "features"])).stdout;
    expect(text).toContain("graph_rerank：关（默认）");
    expect(text).toContain("intention：开（默认）");
    expect(text).toContain("先开启 session_episodes");
  });

  it("prints the plan and changes nothing without --yes", async () => {
    const result = await capturedRun([
      "settings",
      "features",
      "set",
      "graph_rerank=true",
      "episode_recall=true",
    ]);
    expect(result.code).toBe(0);
    expect(transport.put).not.toHaveBeenCalled();
    expect(result.stdout).toContain(
      "memhub settings features set graph_rerank=true episode_recall=true --yes",
    );
  });

  it("sets features with --yes and reports the ones inactive in this mode", async () => {
    transport.put.mockResolvedValue({
      status: 200,
      data: featuresState({ graph_rerank: true, episode_recall: true }, "pro", {
        changed: ["graph_rerank", "episode_recall"],
      }),
    });
    const result = await capturedRun([
      "settings",
      "features",
      "set",
      "graph_rerank=true",
      "episode_recall=true",
      "--yes",
    ]);
    expect(result.code).toBe(0);
    expect(transport.put).toHaveBeenCalledWith(
      "/api/v1/settings/memory-features",
      { graph_rerank: true, episode_recall: true },
      undefined,
    );
    expect(result.stdout).toContain("已更新：graph_rerank、episode_recall");
    expect(result.stdout).toContain(
      "graph_rerank：开（已设置，当前模式下不生效（需要 ultra））",
    );
  });

  it("does nothing when the values are already set", async () => {
    transport.get.mockResolvedValue({
      status: 200,
      data: featuresState({ few_shot: true }),
    });
    const result = await capturedRun([
      "settings",
      "features",
      "set",
      "few_shot=true",
      "--yes",
    ]);
    expect(result.code).toBe(0);
    expect(result.stdout).toContain("没有需要改动的设置");
    expect(transport.put).not.toHaveBeenCalled();
  });

  it("resets the named features, or every set one, to the default", async () => {
    transport.get.mockResolvedValue({
      status: 200,
      data: featuresState({ few_shot: true, intention: false }),
    });
    transport.put.mockResolvedValue({
      status: 200,
      data: featuresState({}, "pro", { changed: ["intention", "few_shot"] }),
    });
    expect(
      (await capturedRun(["settings", "features", "reset", "few_shot"])).code,
    ).toBe(0);
    expect(transport.put).toHaveBeenLastCalledWith(
      "/api/v1/settings/memory-features",
      { few_shot: null },
      undefined,
    );
    const all = await capturedRun(["settings", "features", "reset", "--json"]);
    expect(JSON.parse(all.stdout).state).toBe("committed");
    expect(transport.put).toHaveBeenLastCalledWith(
      "/api/v1/settings/memory-features",
      { intention: null, few_shot: null },
      undefined,
    );
  });

  it("refuses unknown names and malformed values before the Runtime", async () => {
    for (const bad of [
      ["settings", "features", "set"],
      ["settings", "features", "set", "local_rerank=true"],
      ["settings", "features", "set", "graph_rerank=yes"],
      ["settings", "features", "set", "graph_rerank"],
      ["settings", "features", "set", "few_shot=true", "few_shot=false"],
      ["settings", "features", "reset", "nope"],
      ["settings", "features", "toggle"],
    ]) {
      const result = await capturedRun(bad);
      expect(result.code, bad.join(" ")).toBe(2);
    }
    expect(transport.put).not.toHaveBeenCalled();
  });

  it("surfaces a 422 from the Runtime as rejected", async () => {
    transport.put.mockResolvedValue({
      status: 422,
      data: { detail: "invalid_request" },
    });
    const result = await capturedRun([
      "settings",
      "features",
      "set",
      "few_shot=true",
      "--yes",
      "--json",
    ]);
    expect(result.code).toBe(1);
    expect(JSON.parse(result.stdout).state).toBe("rejected");
  });

  it("parses the forms the help shows", () => {
    expect(parseFeaturesCommand([])).toEqual({ kind: "show" });
    expect(parseFeaturesCommand(["set", "intention=false", "--yes"])).toEqual({
      kind: "set",
      values: { intention: false },
      confirmed: true,
    });
    expect(parseFeaturesCommand(["reset"])).toEqual({
      kind: "reset",
      names: [],
    });
  });
});

describe("status pipeline features", () => {
  it("shows the features that differ from the defaults", async () => {
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
        mode: "pro",
        configured_mode: "pro",
        forced_by_env: false,
        last_evolve_at: null,
        graph_build: { state: "idle", users: {} },
        evolve: { mode: "pro", schedule: null, running: [], users: {} },
        features: { episode_recall: true, intention: false },
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
    expect(report.pipeline?.features).toEqual({
      episode_recall: true,
      intention: false,
    });
    const text = renderStatusText(report, "MemHub");
    expect(text).toContain("SDK 功能：episode_recall=开、intention=关");
  });
});

async function capturedRun(arguments_: string[]) {
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
      code: await run(arguments_, { ONE_MEMORY_DATA_DIR: "/synthetic/data" }),
      stdout,
      stderr,
    };
  } finally {
    stdoutSpy.mockRestore();
    stderrSpy.mockRestore();
  }
}
