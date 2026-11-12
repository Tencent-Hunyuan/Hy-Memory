import { chmod, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { run } from "./cli.js";
import { composeStatusReport, renderStatusText } from "./status-report.js";
import { parseBackendsCommand } from "./storage-backends-flow.js";

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

const PATH = "/api/v1/settings/storage-backends";
const TEST_PATH = "/api/v1/settings/storage-backends/test";

const CAPABILITIES = {
  embedding_switch: { state: "works", code: null },
  backup: { state: "works", code: null },
  upgrade_migration: { state: "works", code: null },
};

function state(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    graph: {
      provider: "kuzu",
      url: null,
      username: null,
      database: null,
      has_password: false,
      driver_available: true,
    },
    vector: {
      provider: "chroma",
      host: null,
      port: null,
      collection_prefix: null,
      has_api_key: false,
      driver_available: true,
    },
    is_default: true,
    label: "Kuzu + Chroma",
    namespace: null,
    capabilities: CAPABILITIES,
    health: null,
    providers: { graph: [], vector: [] },
    ...overrides,
  };
}

const CUSTOM = state({
  graph: {
    provider: "neo4j",
    url: "bolt://graph.internal:7687",
    username: "neo4j",
    database: null,
    has_password: true,
    driver_available: true,
  },
  vector: {
    provider: "qdrant",
    host: "vectors.internal",
    port: 6333,
    collection_prefix: "mh1a2b3c4d",
    has_api_key: false,
    driver_available: true,
  },
  is_default: false,
  label: "Neo4j + Qdrant",
  namespace: "mh1a2b3c4d",
  capabilities: {
    ...CAPABILITIES,
    embedding_switch: {
      state: "refused",
      code: "embedding_switch_backend_unsupported",
    },
    upgrade_migration: {
      state: "skipped",
      code: "upgrade_migration_skipped_custom_backend",
    },
  },
});

function probe(
  graphOk = true,
  vectorOk = true,
  extra: Record<string, unknown> = {},
) {
  return {
    ok: graphOk && vectorOk,
    graph: {
      provider: "neo4j",
      ok: graphOk,
      state: graphOk ? "ok" : "failed",
      code: graphOk ? null : "storage_backend_unreachable",
      existing_memory_nodes: 0,
      latency_ms: 4,
      ...extra,
    },
    vector: {
      provider: "qdrant",
      ok: vectorOk,
      state: vectorOk ? "ok" : "failed",
      code: vectorOk ? null : "storage_backend_auth_failed",
    },
  };
}

let directory: string;

beforeEach(async () => {
  directory = await mkdtemp(path.join(tmpdir(), "memhub-backends-"));
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
    url === PATH ? { status: 200, data: state() } : { status: 404, data: {} },
  );
  transport.post.mockImplementation(async (url: string) =>
    url === TEST_PATH
      ? {
          status: 200,
          data: {
            schema_version: 1,
            label: "Neo4j + Qdrant",
            probe: probe(),
            graph_adoption_required: false,
          },
        }
      : { status: 404, data: {} },
  );
});

afterEach(async () => {
  await rm(directory, { recursive: true, force: true });
});

async function fixtureFile(name: string, value: string, mode = 0o600) {
  const file = path.join(directory, name);
  await writeFile(file, `${value}\n`);
  await chmod(file, mode);
  return file;
}

const SET = [
  "settings",
  "backends",
  "set",
  "--graph",
  "neo4j",
  "--graph-url",
  "bolt://graph.internal:7687",
  "--graph-user",
  "neo4j",
  "--vector",
  "qdrant",
  "--vector-host",
  "vectors.internal",
];

describe("parseBackendsCommand", () => {
  it("accepts the documented shapes and refuses the rest", () => {
    expect(parseBackendsCommand([])).toEqual({ kind: "show" });
    expect(parseBackendsCommand(["show", "--json"])).toEqual({ kind: "show" });
    expect(parseBackendsCommand(["reset", "--yes"])).toEqual({
      kind: "reset",
      confirmed: true,
    });
    expect(
      parseBackendsCommand(["set", "--vector", "qdrant", "--vector-port", "1"]),
    ).toMatchObject({ kind: "set", vector: { provider: "qdrant", port: 1 } });
    for (const bad of [
      ["set"],
      ["set", "--graph-url", "bolt://x"],
      ["set", "--graph", "orientdb"],
      ["set", "--vector", "qdrant", "--vector-port", "70000"],
      ["set", "--vector", "qdrant", "--vector-host"],
      ["set", "--vector", "qdrant", "--unknown", "x"],
      ["test", "--vector", "qdrant", "--yes"],
      [
        "set",
        "--graph",
        "neo4j",
        "--graph-password-file",
        "-",
        "--vector",
        "qdrant",
        "--vector-api-key-file",
        "-",
      ],
      ["reset", "--adopt-existing-graph"],
      ["drop"],
    ]) {
      expect(parseBackendsCommand(bad)).toBeNull();
    }
  });
});

describe("memhub settings backends", () => {
  it("shows the backends, never a secret, and what works on them", async () => {
    transport.get.mockResolvedValue({
      status: 200,
      data: { ...CUSTOM, health: probe() },
    });
    const text = (await capturedRun(["settings", "backends"])).stdout;
    expect(text).toContain("存储后端：Neo4j + Qdrant（通过 CLI 配置）");
    expect(text).toContain("bolt://graph.internal:7687");
    expect(text).toContain("密码已保存在钥匙串");
    expect(text).toContain("集合前缀 mh1a2b3c4d");
    expect(text).toContain("连接测试：通过");
    expect(text).toContain(
      "更换向量模型：不支持（embedding_switch_backend_unsupported）",
    );
    expect(text).toContain(
      "升级迁移：跳过（upgrade_migration_skipped_custom_backend）",
    );
    const json = await capturedRun(["settings", "backends", "show", "--json"]);
    expect(json.code).toBe(0);
    const document = JSON.parse(json.stdout);
    expect(document).toMatchObject({
      schema_version: 1,
      command: "settings backends",
      verb: "show",
      state: "ok",
    });
    expect(document.backends.label).toBe("Neo4j + Qdrant");
  });

  it("tests a target without saving it", async () => {
    const graphCredentialPath = await fixtureFile("graph-cred", "graph-pass");
    const result = await capturedRun([
      ...SET.map((value) => (value === "set" ? "test" : value)),
      "--graph-password-file",
      graphCredentialPath,
      "--json",
    ]);
    expect(result.code).toBe(0);
    expect(transport.post).toHaveBeenCalledWith(
      TEST_PATH,
      {
        graph: {
          provider: "neo4j",
          url: "bolt://graph.internal:7687",
          username: "neo4j",
          password: "graph-pass",
        },
        vector: { provider: "qdrant", host: "vectors.internal" },
      },
      undefined,
    );
    expect(transport.put).not.toHaveBeenCalled();
    expect(JSON.parse(result.stdout)).toMatchObject({
      state: "tested",
      target_label: "Neo4j + Qdrant",
    });
    expect(result.stdout).not.toContain("graph-pass\n");
  });

  it("previews with the probe and the not-moved notice, then sets with --yes", async () => {
    const preview = await capturedRun(SET);
    expect(preview.code).toBe(0);
    expect(transport.put).not.toHaveBeenCalled();
    expect(preview.stdout).toContain(
      "将把存储后端从 Kuzu + Chroma 改为 Neo4j + Qdrant。",
    );
    expect(preview.stdout).toContain("连接测试：通过");
    expect(preview.stdout).toContain("不会被迁移到新后端");
    expect(preview.stdout).toContain("原来的数据不会被删除或改写");
    expect(preview.stdout).toContain(`memhub ${SET.join(" ")} --yes`);

    transport.put.mockResolvedValue({
      status: 200,
      data: {
        ...CUSTOM,
        changed: true,
        previous_label: "Kuzu + Chroma",
        notice: "existing_memories_not_moved",
        probe: probe(),
      },
    });
    const committed = await capturedRun([...SET, "--yes", "--json"]);
    expect(committed.code).toBe(0);
    expect(transport.put).toHaveBeenCalledWith(
      PATH,
      {
        graph: {
          provider: "neo4j",
          url: "bolt://graph.internal:7687",
          username: "neo4j",
        },
        vector: { provider: "qdrant", host: "vectors.internal" },
      },
      undefined,
    );
    expect(JSON.parse(committed.stdout)).toMatchObject({
      state: "committed",
      changed: true,
      previous_label: "Kuzu + Chroma",
      notice: "existing_memories_not_moved",
    });
    const text = await capturedRun([...SET, "--yes"]);
    expect(text.stdout).toContain(
      "已把存储后端从 Kuzu + Chroma 改为 Neo4j + Qdrant。",
    );
    expect(text.stdout).toContain("不会被迁移到新后端");
  });

  it("never saves a backend whose connection test failed", async () => {
    transport.post.mockResolvedValue({
      status: 200,
      data: {
        schema_version: 1,
        label: "Neo4j + Qdrant",
        probe: probe(false, true),
        graph_adoption_required: false,
      },
    });
    const result = await capturedRun([...SET, "--yes"]);
    expect(result.code).toBe(1);
    expect(transport.put).not.toHaveBeenCalled();
    expect(result.stdout).toContain("连接测试没有通过，设置未保存。");
    expect(result.stdout).toContain(
      "图数据库（neo4j）：连接失败 storage_backend_unreachable",
    );
    expect(result.stdout).toContain("检查地址、端口");
  });

  it("asks to adopt a graph database that already holds a graph", async () => {
    transport.post.mockResolvedValue({
      status: 200,
      data: {
        schema_version: 1,
        label: "Neo4j + Qdrant",
        probe: probe(true, true, { existing_memory_nodes: 12 }),
        graph_adoption_required: true,
      },
    });
    const refused = await capturedRun([...SET, "--yes", "--json"]);
    expect(refused.code).toBe(1);
    expect(JSON.parse(refused.stdout)).toMatchObject({
      state: "rejected",
      code: "storage_backend_graph_not_empty",
    });
    expect(JSON.parse(refused.stdout).next_step).toContain(
      "--adopt-existing-graph",
    );
    expect(transport.put).not.toHaveBeenCalled();

    transport.put.mockResolvedValue({
      status: 200,
      data: { ...CUSTOM, changed: true, previous_label: "Kuzu + Chroma" },
    });
    const adopted = await capturedRun([
      ...SET,
      "--adopt-existing-graph",
      "--yes",
    ]);
    expect(adopted.code).toBe(0);
    expect(transport.put.mock.calls[0][1]).toMatchObject({
      adopt_existing_graph: true,
    });
  });

  it("maps Core's refusals to a code and a next step", async () => {
    for (const [code, status, hint] of [
      ["storage_backend_faiss_unsupported", 422, "不支持 FAISS"],
      ["storage_backend_vector_host_required", 422, "--vector-host"],
      ["embedding_maintenance_in_progress", 409, "向量模型切换"],
      ["memory_lifecycle_busy", 409, "记忆进化"],
      ["secure_storage_unavailable", 503, "钥匙串"],
    ] as const) {
      transport.put.mockResolvedValueOnce({
        status,
        data: { detail: code },
      });
      const result = await capturedRun([...SET, "--yes"]);
      expect(result.code, code).toBe(1);
      expect(result.stdout, code).toContain(code);
      expect(result.stdout, code).toContain(hint);
    }
    transport.post.mockResolvedValueOnce({
      status: 422,
      data: { detail: "storage_backend_faiss_unsupported" },
    });
    const faiss = await capturedRun([
      "settings",
      "backends",
      "test",
      "--vector",
      "faiss",
      "--json",
    ]);
    expect(faiss.code).toBe(1);
    expect(JSON.parse(faiss.stdout).code).toBe(
      "storage_backend_faiss_unsupported",
    );
  });

  it("reads secrets from owner-only files only", async () => {
    const open = await fixtureFile("open-key", "vector-key", 0o644);
    const refused = await capturedRun([
      "settings",
      "backends",
      "set",
      "--vector",
      "qdrant",
      "--vector-host",
      "v",
      "--vector-api-key-file",
      open,
      "--json",
    ]);
    expect(refused.code).toBe(1);
    expect(JSON.parse(refused.stdout).code).toBe(
      "storage_backend_secret_file_invalid",
    );
    expect(transport.post).not.toHaveBeenCalled();

    const closed = await fixtureFile("key", "vector-key");
    await capturedRun([
      "settings",
      "backends",
      "set",
      "--vector",
      "qdrant",
      "--vector-host",
      "v",
      "--vector-api-key-file",
      closed,
    ]);
    expect(transport.post.mock.calls[0][1]).toEqual({
      vector: { provider: "qdrant", host: "v", api_key: "vector-key" },
    });
  });

  it("resets to Kuzu + Chroma only with --yes", async () => {
    transport.get.mockResolvedValue({ status: 200, data: CUSTOM });
    const preview = await capturedRun(["settings", "backends", "reset"]);
    expect(preview.code).toBe(0);
    expect(transport.delete).not.toHaveBeenCalled();
    expect(preview.stdout).toContain("改为 Kuzu + Chroma");
    expect(preview.stdout).toContain("不会被迁移到新后端");
    transport.delete.mockResolvedValue({
      status: 200,
      data: {
        ...state(),
        changed: true,
        previous_label: "Neo4j + Qdrant",
        notice: "existing_memories_not_moved",
      },
    });
    const reset = await capturedRun(["settings", "backends", "reset", "--yes"]);
    expect(reset.code).toBe(0);
    expect(transport.delete).toHaveBeenCalledWith(PATH, undefined);
    expect(reset.stdout).toContain(
      "已把存储后端从 Neo4j + Qdrant 改为 Kuzu + Chroma。",
    );
    transport.get.mockResolvedValue({ status: 200, data: state() });
    const unchanged = await capturedRun([
      "settings",
      "backends",
      "reset",
      "--yes",
    ]);
    expect(unchanged.stdout).toContain("已经是默认的存储后端");
    expect(transport.delete).toHaveBeenCalledTimes(1);
  });
});

describe("status storage backends", () => {
  async function report(storage: unknown) {
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
        features: {},
        ...(storage === undefined ? {} : { storage_backends: storage }),
      },
    };
    return composeStatusReport({
      dataDir: "/tmp/isolated",
      runtime: { state: "running", code: "ok", host: "127.0.0.1", port: 1 },
      background: null,
      reader: (async (requested: string) =>
        requested === "/api/v1/memory-processing"
          ? { state: "ok", data: processing }
          : { state: "unavailable", code: "x" }) as never,
    });
  }

  it("shows the default pair, a custom pair and a failing server", async () => {
    const plain = await report({
      graph: "kuzu",
      vector: "chroma",
      is_default: true,
      label: "Kuzu + Chroma",
      health: "local",
      health_code: null,
    });
    expect(renderStatusText(plain, "MemHub")).toContain(
      "存储后端：Kuzu + Chroma（默认）",
    );
    expect(plain.summary.degraded).not.toContain("storage_backends");

    const custom = await report({
      graph: "neo4j",
      vector: "qdrant",
      is_default: false,
      label: "Neo4j + Qdrant",
      health: "ok",
      health_code: null,
    });
    expect(custom.pipeline?.storage_backends?.label).toBe("Neo4j + Qdrant");
    expect(renderStatusText(custom, "MemHub")).toContain(
      "存储后端：Neo4j + Qdrant（通过 CLI 配置，连接正常）",
    );

    const failed = await report({
      graph: "neo4j",
      vector: "chroma",
      is_default: false,
      label: "Neo4j + Chroma",
      health: "failed",
      health_code: "storage_backend_unreachable",
    });
    expect(failed.summary.degraded).toContain("storage_backends");
    expect(renderStatusText(failed, "MemHub")).toContain(
      "存储后端：Neo4j + Chroma（通过 CLI 配置，连接失败：storage_backend_unreachable）",
    );

    const unknown = await report({
      graph: "kuzu",
      vector: "qdrant",
      is_default: false,
      label: "Kuzu + Qdrant",
      health: "unknown",
      health_code: null,
    });
    expect(unknown.summary.degraded).not.toContain("storage_backends");
  });

  it("prints no line for an older Core without the field", async () => {
    const older = await report(undefined);
    expect(older.pipeline?.storage_backends).toBeNull();
    expect(renderStatusText(older, "MemHub")).not.toContain("存储后端");
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
