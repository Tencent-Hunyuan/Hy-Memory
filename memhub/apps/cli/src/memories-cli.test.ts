import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
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
}));
vi.mock("@one-memory/runtime-control", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@one-memory/runtime-control")>()),
  ...runtime,
}));

const REQUEST = "33333333-3333-4333-8333-333333333333";
const EVIDENCE = "44444444-4444-4444-8444-444444444444";

function addReceipt(state = "pending", memoryIds: string[] = []) {
  return {
    evidence_id: EVIDENCE,
    text: "Prefers tea",
    scope: "all-apps",
    created_at: "2026-10-27T00:00:00Z",
    state,
    task_id: null,
    memory_ids: memoryIds,
    error: null,
  };
}

function memoryRecord(overrides: Record<string, unknown> = {}) {
  return {
    memory_id: "exp-1",
    content: "deploy\nrun make",
    layer: "experience",
    status: "active",
    is_latest: true,
    supersedes: [],
    superseded_by: [],
    tags: [],
    owner: null,
    source_raw_ids: [],
    user_id: "local",
    agent_id: "",
    session_id: null,
    source: "codebuddy",
    memory_at: null,
    gmt_created: 1_790_000_000,
    scene: "working",
    kind: "experience",
    variant: "working",
    task: "deploy",
    solution: "run make",
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  transport.get.mockReset();
  transport.post.mockReset();
  transport.put.mockReset();
  transport.patch.mockReset();
  transport.delete.mockReset();
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

describe("memories add", () => {
  it("adds one memory directly and prints the receipt with its request id", async () => {
    transport.post.mockResolvedValue({ status: 201, data: addReceipt() });
    const result = await capturedRun([
      "memories",
      "add",
      "Prefers tea",
      "--json",
    ]);
    expect(result.code).toBe(0);
    const [url, body] = transport.post.mock.calls[0];
    expect(url).toBe("/api/v2/memories");
    expect(body).toMatchObject({ text: "Prefers tea", scope: "all-apps" });
    expect(body.request_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(JSON.parse(result.stdout)).toMatchObject({
      schema_version: 2,
      command: "memories add",
      state: "accepted",
      request_id: body.request_id,
      receipt: { evidence_id: EVIDENCE, state: "pending" },
    });
  });

  it("reuses --request-id so a retry is idempotent, and says so in text", async () => {
    transport.post.mockResolvedValue({ status: 201, data: addReceipt() });
    const result = await capturedRun([
      "memories",
      "add",
      "Prefers tea",
      "--request-id",
      REQUEST,
    ]);
    expect(result.code).toBe(0);
    expect(transport.post.mock.calls[0][1].request_id).toBe(REQUEST);
    expect(result.stdout).toContain(`request_id: ${REQUEST}`);
    expect(result.stdout).toContain(`memhub memories add-status ${REQUEST}`);
  });

  it("reads the text from a bounded file", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "om-memory-add-"));
    const file = path.join(directory, "memory.txt");
    await writeFile(file, "Prefers tea\n", { mode: 0o600 });
    transport.post.mockResolvedValue({ status: 201, data: addReceipt() });
    const result = await capturedRun([
      "memories",
      "add",
      "--content-file",
      file,
      "--json",
    ]);
    expect(result.code).toBe(0);
    expect(transport.post.mock.calls[0][1].text).toBe("Prefers tea");
  });

  it("refuses bad input before contacting Core", async () => {
    for (const argv of [
      ["memories", "add"],
      ["memories", "add", "a", "--content-file", "/tmp/x"],
      ["memories", "add", "a", "--request-id", "not-a-uuid"],
      ["memories", "add", "   "],
      ["memories", "add", "x".repeat(12_001)],
    ]) {
      expect((await capturedRun(argv)).code).toBe(2);
    }
    expect(transport.post).not.toHaveBeenCalled();
    expect(runtime.inspectRuntimeStatus).not.toHaveBeenCalled();
  });

  it("reports a Core conflict as rejected, on stdout, exit 1", async () => {
    transport.post.mockResolvedValue({
      status: 409,
      data: { detail: "memory_add_conflict" },
    });
    const result = await capturedRun([
      "memories",
      "add",
      "Other text",
      "--request-id",
      REQUEST,
      "--json",
    ]);
    expect(result.code).toBe(1);
    expect(JSON.parse(result.stdout)).toMatchObject({
      state: "rejected",
      code: "memory_add_conflict",
      request_id: REQUEST,
    });
  });

  it("never reports a malformed success body as accepted", async () => {
    transport.post.mockResolvedValue({
      status: 201,
      data: { evidence_id: "x" },
    });
    const result = await capturedRun(["memories", "add", "tea", "--json"]);
    expect(result.code).toBe(1);
    expect(JSON.parse(result.stdout).state).toBe("unknown");
  });

  it("answers a stopped Runtime with exit 3 and writes nothing", async () => {
    runtime.inspectRuntimeStatus.mockResolvedValue({
      state: "stopped",
      code: "runtime_not_running",
    });
    const result = await capturedRun(["memories", "add", "tea", "--json"]);
    expect(result.code).toBe(3);
    expect(JSON.parse(result.stdout).code).toBe("runtime_not_running");
    expect(transport.post).not.toHaveBeenCalled();
  });
});

describe("memories add-status", () => {
  it("reads the addition by request id", async () => {
    transport.get.mockResolvedValue({
      status: 200,
      data: addReceipt("ready", ["m-1"]),
    });
    const result = await capturedRun([
      "memories",
      "add-status",
      REQUEST,
      "--json",
    ]);
    expect(result.code).toBe(0);
    expect(transport.get).toHaveBeenCalledWith(
      `/api/v2/memories/additions/${REQUEST}`,
      undefined,
    );
    expect(JSON.parse(result.stdout)).toMatchObject({
      command: "memories add-status",
      state: "accepted",
      receipt: { state: "ready", memory_ids: ["m-1"] },
    });
  });

  it("reports an unknown request id as not_found, exit 1", async () => {
    transport.get.mockResolvedValue({
      status: 404,
      data: { detail: "memory_add_not_found" },
    });
    const result = await capturedRun(["memories", "status", REQUEST]);
    expect(result.code).toBe(1);
    expect(result.stdout).toContain("未找到该 request id");
  });

  it("refuses a non-UUID request id", async () => {
    expect((await capturedRun(["memories", "add-status", "abc"])).code).toBe(2);
    expect(transport.get).not.toHaveBeenCalled();
  });
});

describe("memories update / delete pass the namespace", () => {
  it("updates an experience with kind, variant and task, directly", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "om-memory-update-"));
    const file = path.join(directory, "solution.txt");
    await writeFile(file, "run make deploy\n", { mode: 0o600 });
    transport.put.mockResolvedValue({ status: 200, data: memoryRecord() });
    const result = await capturedRun([
      "memories",
      "update",
      "exp-1",
      "--content-file",
      file,
      "--kind",
      "experience",
      "--variant",
      "working",
      "--task",
      "deploy",
      "--json",
    ]);
    expect(result.code).toBe(0);
    expect(transport.put).toHaveBeenCalledWith(
      "/api/v2/memories/exp-1",
      {
        content: "run make deploy",
        kind: "experience",
        variant: "working",
        task: "deploy",
      },
      undefined,
    );
    expect(JSON.parse(result.stdout)).toMatchObject({
      schema_version: 2,
      state: "committed",
      operation: "update",
    });
  });

  it("refuses --task on anything but an experience, and a lone --variant", async () => {
    for (const argv of [
      ["memories", "update", "m-1", "--content-file", "/tmp/x", "--task", "t"],
      [
        "memories",
        "update",
        "m-1",
        "--content-file",
        "/tmp/x",
        "--kind",
        "fact",
        "--variant",
        "coding",
        "--task",
        "t",
      ],
      ["memories", "delete", "m-1", "--variant", "coding"],
      ["memories", "delete", "m-1", "--kind", "chat", "--variant", "coding"],
      ["memories", "show", "m-1", "--kind", "nope"],
    ]) {
      expect((await capturedRun(argv)).code).toBe(2);
    }
    expect(transport.put).not.toHaveBeenCalled();
    expect(transport.delete).not.toHaveBeenCalled();
  });

  it("previews a delete without --yes and changes nothing", async () => {
    const result = await capturedRun([
      "memories",
      "delete",
      "fact-1",
      "--kind",
      "fact",
      "--variant",
      "coding",
      "--json",
    ]);
    expect(result.code).toBe(0);
    expect(JSON.parse(result.stdout)).toMatchObject({
      schema_version: 2,
      command: "memories delete",
      state: "preview",
      target: "fact-1",
      applied: false,
    });
    expect(JSON.parse(result.stdout).next_step).toContain(
      "--kind fact --variant coding --yes",
    );
    expect(transport.delete).not.toHaveBeenCalled();
    expect(runtime.inspectRuntimeStatus).not.toHaveBeenCalled();
  });

  it("deletes with --yes (or the legacy --confirm) and names the namespace", async () => {
    transport.delete.mockResolvedValue({ status: 204, data: "" });
    for (const flag of ["--yes", "--confirm"]) {
      const result = await capturedRun([
        "memories",
        "delete",
        "fact-1",
        "--kind",
        "fact",
        "--variant",
        "coding",
        flag,
        "--json",
      ]);
      expect(result.code).toBe(0);
      expect(JSON.parse(result.stdout)).toMatchObject({
        schema_version: 2,
        state: "committed",
        operation: "delete",
      });
    }
    expect(transport.delete).toHaveBeenCalledWith(
      "/api/v2/memories/fact-1?kind=fact&variant=coding",
      undefined,
    );
  });

  it("explains an ambiguous id instead of a bare code", async () => {
    transport.delete.mockResolvedValue({
      status: 409,
      data: { detail: "memory_id_ambiguous" },
    });
    const result = await capturedRun(["memories", "delete", "dup", "--yes"]);
    expect(result.code).toBe(1);
    expect(result.stdout).toContain("--kind");
  });

  it("shows a memory in its namespace", async () => {
    transport.get.mockResolvedValue({ status: 200, data: memoryRecord() });
    const result = await capturedRun([
      "memories",
      "show",
      "exp-1",
      "--kind",
      "experience",
      "--variant",
      "working",
      "--json",
    ]);
    expect(result.code).toBe(0);
    expect(transport.get).toHaveBeenCalledWith("/api/v2/memories/exp-1", {
      params: { kind: "experience", variant: "working" },
    });
    const document = JSON.parse(result.stdout);
    expect(document.schema_version).toBe(2);
    expect(document).not.toHaveProperty("evidence");
    expect(document).not.toHaveProperty("history");
    expect(document.memory).toMatchObject({
      kind: "experience",
      variant: "working",
      task: "deploy",
    });
  });
});

describe("memories list filters", () => {
  const page = {
    memories: [memoryRecord()],
    total: 1,
    limit: 100,
    offset: 0,
    search_window: 200,
    window_id: "w1",
    window_reset: false,
  };

  it("sends --scene, --source and --query to the library", async () => {
    transport.get.mockResolvedValue({ status: 200, data: page });
    const result = await capturedRun([
      "memories",
      "list",
      "--scene",
      "working",
      "--source",
      "codebuddy",
      "--query",
      "deploy",
      "--json",
    ]);
    expect(result.code).toBe(0);
    expect(transport.get).toHaveBeenCalledWith("/api/v2/memory-library", {
      params: {
        offset: 0,
        limit: 100,
        scene: "working",
        source: "codebuddy",
        query: "deploy",
      },
    });
    const document = JSON.parse(result.stdout);
    expect(document.schema_version).toBe(2);
    expect(document.page).toMatchObject({
      state: "ok",
      scene: "working",
      query: "deploy",
      search_window: 200,
    });
    expect(document.memories[0]).toMatchObject({
      kind: "experience",
      variant: "working",
    });
  });

  it("omits scene for the default all", async () => {
    transport.get.mockResolvedValue({ status: 200, data: page });
    await capturedRun(["memories", "list", "--scene", "all", "--json"]);
    expect(transport.get.mock.calls[0][1].params).not.toHaveProperty("scene");
  });

  it("validates --status, --scene and --query locally instead of reporting a busy backend", async () => {
    for (const argv of [
      ["memories", "list", "--status", "bogus"],
      ["memories", "list", "--scene", "normal"],
      ["memories", "list", "--query", "x", "--status", "archived"],
      ["memories", "list", "--source", "Bad Source"],
    ]) {
      expect((await capturedRun(argv)).code).toBe(2);
    }
    expect(transport.get).not.toHaveBeenCalled();
  });

  it("reports a Core 422 as rejected, not as 后台正忙", async () => {
    transport.get.mockResolvedValue({
      status: 422,
      data: { detail: "sdk_search_supports_active_status_only" },
    });
    const result = await capturedRun(["memories", "list"]);
    expect(result.code).toBe(1);
    expect(result.stdout).not.toContain("后台正忙");
    expect(result.stdout).toContain("sdk_search_supports_active_status_only");
  });
});

describe("graph-width", () => {
  it("runs a non-destructive decision directly", async () => {
    transport.post.mockResolvedValue({
      status: 200,
      data: { archived_path: null, exported_path: "/tmp/x", mismatch: null },
    });
    const result = await capturedRun([
      "graph-width",
      "--decision",
      "export_first",
      "--json",
    ]);
    expect(result.code).toBe(0);
    expect(transport.post).toHaveBeenCalledWith(
      "/api/v1/graph-width",
      { decision: "export_first" },
      undefined,
    );
    expect(JSON.parse(result.stdout).state).toBe("committed");
  });

  it("previews rebuild_with_new without --yes and applies it with --yes", async () => {
    const preview = await capturedRun([
      "graph-width",
      "--decision",
      "rebuild_with_new",
      "--json",
    ]);
    expect(preview.code).toBe(0);
    expect(JSON.parse(preview.stdout)).toMatchObject({
      state: "preview",
      applied: false,
    });
    expect(transport.post).not.toHaveBeenCalled();
    transport.post.mockResolvedValue({
      status: 200,
      data: { archived_path: "/tmp/old", exported_path: null, mismatch: null },
    });
    const applied = await capturedRun([
      "graph",
      "width",
      "--decision",
      "rebuild_with_new",
      "--yes",
      "--json",
    ]);
    expect(applied.code).toBe(0);
    expect(transport.post).toHaveBeenCalledWith(
      "/api/v1/graph-width",
      { decision: "rebuild_with_new" },
      undefined,
    );
  });
});

describe("removed commands", () => {
  it.each([
    ["notes", "add", "--content-file", "/tmp/x"],
    ["notes", "process", REQUEST, "--confirm"],
    ["calls", "clear", "--all", "--confirm"],
    ["processing", "extract", "--evidence", REQUEST, "--confirm"],
    ["extraction", "set", "--window", "6m", "--confirm"],
    ["import-file", "upload", "--file", "/tmp/x", "--confirm"],
  ])("%s is an unknown command that writes nothing", async (...argv) => {
    const result = await capturedRun(argv as string[]);
    expect(result.code).toBe(2);
    expect(result.stderr).toContain("未知命令");
    expect(transport.post).not.toHaveBeenCalled();
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
      code: await run(arguments_, {
        ONE_MEMORY_DATA_DIR: "/synthetic/data",
      }),
      stdout,
      stderr,
    };
  } finally {
    stdoutSpy.mockRestore();
    stderrSpy.mockRestore();
  }
}
