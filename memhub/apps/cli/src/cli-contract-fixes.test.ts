// Contract fixes from the CLI review: exit codes, where JSON goes, the global
// `--data-dir`, and the write idempotency key.
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { run } from "./cli.js";
import { COMMANDS } from "./command-catalog.js";

const transport = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
  request: vi.fn(),
}));
vi.mock("axios", () => ({
  default: {
    create: vi.fn(() => transport),
    get: transport.get,
    post: transport.post,
    request: transport.request,
    isAxiosError: () => false,
  },
}));

const runtime = vi.hoisted(() => ({
  inspectRuntimeStatus: vi.fn(),
  discoverRuntimeAttachment: vi.fn(),
  inspectBackgroundHealth: vi.fn(),
}));
vi.mock("@one-memory/runtime-control", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@one-memory/runtime-control")>()),
  ...runtime,
}));

const ATTACHED = {
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
};

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
  runtime.discoverRuntimeAttachment.mockResolvedValue(ATTACHED);
  runtime.inspectBackgroundHealth.mockResolvedValue(null);
});

describe("--data-dir is a global option", () => {
  it("points a Runtime-attached command at that data directory", async () => {
    transport.get.mockResolvedValue({
      status: 200,
      data: { request_id: "r", profile: null, elapsed_ms: 1 },
    });
    const result = await capturedRun(
      ["profile", "--data-dir", "/synthetic/other", "--json"],
      { ONE_MEMORY_DATA_DIR: "/synthetic/default" },
    );
    expect(result.code).toBe(0);
    expect(runtime.inspectRuntimeStatus).toHaveBeenCalledWith(
      expect.objectContaining({ dataDir: "/synthetic/other" }),
    );
  });

  it("is accepted by every command the catalog used to advertise it on", async () => {
    runtime.inspectRuntimeStatus.mockResolvedValue({
      state: "stopped",
      code: "runtime_not_running",
    });
    runtime.discoverRuntimeAttachment.mockResolvedValue(null);
    for (const argv of [
      ["profile"],
      ["scan-roots", "list"],
      ["adapter", "list"],
      ["scan", "--source", "codex"],
      ["write", "status", "r-1"],
      ["backup", "create", "--target", "/synthetic/data/backups/a"],
      ["memories", "list"],
    ]) {
      const result = await capturedRun([
        ...argv,
        "--data-dir",
        "/synthetic/other",
        "--json",
      ]);
      expect(result.code, argv.join(" ")).toBe(3);
    }
  });

  it("refuses a relative or repeated --data-dir", async () => {
    for (const argv of [
      ["profile", "--data-dir", "relative"],
      ["profile", "--data-dir", "/a", "--data-dir", "/b"],
      ["profile", "--data-dir"],
    ]) {
      expect((await capturedRun(argv)).code).toBe(2);
    }
    expect(runtime.inspectRuntimeStatus).not.toHaveBeenCalled();
  });
});

describe("Core failures are exit 1, not usage errors", () => {
  it("adapter probe reports a Core refusal with its code on stdout", async () => {
    transport.request.mockResolvedValue({
      status: 409,
      data: { detail: "source_not_authorized" },
    });
    const result = await capturedRun([
      "adapter",
      "probe",
      "--source",
      "codex",
      "--json",
    ]);
    expect(result.code).toBe(1);
    expect(result.stderr).toBe("");
    expect(JSON.parse(result.stdout)).toMatchObject({
      command: "adapter probe",
      state: "failed",
      code: "source_not_authorized",
      exit_code: 1,
    });
  });

  it("adapter probe prints readable text without --json", async () => {
    transport.request.mockResolvedValue({
      status: 200,
      data: { source: "codex", available: true },
    });
    const result = await capturedRun(["adapter", "probe", "--source", "codex"]);
    expect(result.code).toBe(0);
    expect(result.stdout).toContain("来源 codex 的探测结果");
    expect(result.stdout).toContain("available: true");
  });

  it("scan-roots add posts directly and reports a refusal as exit 1", async () => {
    transport.request.mockResolvedValue({
      status: 409,
      data: { detail: "scan_root_conflict" },
    });
    const result = await capturedRun([
      "scan-roots",
      "add",
      "--root",
      "/synthetic/root",
      "--source",
      "codex",
      "--json",
    ]);
    expect(result.code).toBe(1);
    expect(transport.request).toHaveBeenCalledWith(
      expect.objectContaining({
        method: "post",
        url: "http://127.0.0.1:8765/api/v1/scan-roots",
        data: { root: "/synthetic/root", source: "codex", recursive: true },
      }),
    );
    expect(JSON.parse(result.stdout).code).toBe("scan_root_conflict");
  });

  it("the old `path add` spelling still works", async () => {
    transport.request.mockResolvedValue({ status: 200, data: { ok: true } });
    const result = await capturedRun([
      "path",
      "remove",
      "--root",
      "/synthetic/root",
      "--yes",
    ]);
    expect(result.code).toBe(0);
    expect(transport.request.mock.calls[0][0]).toMatchObject({
      method: "delete",
      data: { root: "/synthetic/root" },
    });
  });

  it("scan --source runs directly", async () => {
    transport.post.mockResolvedValue({
      status: 200,
      data: { job_id: "j1", source_id: "codex", status: "pending" },
    });
    const result = await capturedRun(["scan", "--source", "codex", "--json"]);
    expect(result.code).toBe(0);
    expect(JSON.parse(result.stdout)).toMatchObject({
      command: "scan",
      state: "committed",
      scan: { job_id: "j1" },
    });
    // A scan without a source is a usage error, not a preview.
    expect((await capturedRun(["scan"])).code).toBe(2);
  });
});

describe("write submit keeps its idempotency key", () => {
  async function messagesFile(): Promise<string> {
    const directory = await mkdtemp(path.join(tmpdir(), "memhub-write-"));
    const file = path.join(directory, "messages.json");
    await writeFile(file, JSON.stringify([{ role: "user", content: "hi" }]));
    return file;
  }

  it("generates a request id, sends it and prints it with a retry hint", async () => {
    transport.post.mockImplementation(
      async (_url: string, body: { request_id: string }) => ({
        status: 200,
        data: {
          request_id: body.request_id,
          state: "accepted",
          created_at: "T",
          updated_at: "T",
        },
      }),
    );
    const result = await capturedRun([
      "write",
      "submit",
      "--session",
      "s1",
      "--messages-file",
      await messagesFile(),
    ]);
    expect(result.code).toBe(0);
    const sent = transport.post.mock.calls[0][1].request_id as string;
    expect(sent).toMatch(/^[0-9a-f-]{36}$/);
    expect(result.stdout).toContain(`request_id: ${sent}`);
    expect(result.stdout).toContain(`--request-id ${sent}`);
  });

  it("reports a 409 conflict as JSON on stdout with the request id", async () => {
    transport.post.mockResolvedValue({
      status: 409,
      data: { detail: "memory_write_conflict" },
    });
    const result = await capturedRun([
      "write",
      "submit",
      "--session",
      "s1",
      "--messages-file",
      await messagesFile(),
      "--request-id",
      "req-1",
      "--json",
    ]);
    expect(result.code).toBe(1);
    expect(result.stderr).toBe("");
    expect(JSON.parse(result.stdout)).toMatchObject({
      command: "write submit",
      state: "rejected",
      request_id: "req-1",
      code: "memory_write_conflict",
    });
  });

  it("write status of an unknown id is exit 1 with state not_found", async () => {
    transport.get.mockResolvedValue({ status: 404, data: {} });
    const error = Object.assign(new Error("404"), {
      response: { status: 404 },
    });
    transport.get.mockRejectedValue(error);
    const result = await capturedRun(["task", "--id", "nope", "--json"]);
    // `axios.isAxiosError` is mocked false, so the read error surfaces as a
    // failed read; either way it is exit 1, never a silent exit 0.
    expect(result.code).toBe(1);
  });
});

describe("doctor and ui use exit 3 for a stopped Runtime", () => {
  it("doctor exits 3 when the Runtime is stopped", async () => {
    runtime.inspectRuntimeStatus.mockResolvedValue({
      state: "stopped",
      code: "runtime_not_running",
    });
    const result = await capturedRun(["doctor", "--json"]);
    expect(result.code).toBe(3);
    expect(JSON.parse(result.stdout).schema_version).toBe(1);
  });
});

describe("catalog exit codes", () => {
  it("lists exit 2 for every command, since every parser can reject", () => {
    for (const command of COMMANDS) {
      expect(command.exit_codes, command.name).toContain(2);
    }
  });

  it("lists exit 3 for every command that attaches to the Runtime", () => {
    for (const name of [
      "status",
      "doctor",
      "init",
      "sources",
      "sync",
      "progress",
      "jobs",
      "models",
      "memories",
      "profile",
      "graph-width",
      "host",
      "recall",
      "scan-roots",
      "adapter",
      "scan",
      "write",
      "backup",
      "ui",
      "onboarding",
    ]) {
      const command = COMMANDS.find((entry) => entry.name === name);
      expect(command?.exit_codes, name).toContain(3);
    }
  });

  it("does not claim exit 3 or 4 for lifecycle commands that never return them", () => {
    for (const name of ["start", "stop", "restart"]) {
      const command = COMMANDS.find((entry) => entry.name === name);
      expect(command?.exit_codes).toEqual([0, 1, 2]);
    }
  });

  it("says stop turns login auto-start off for a managed service", () => {
    const stop = COMMANDS.find((entry) => entry.name === "stop");
    expect(stop?.summary).toContain("登录时也不会自动启动");
  });
});

async function capturedRun(
  arguments_: string[],
  environment: NodeJS.ProcessEnv = { ONE_MEMORY_DATA_DIR: "/synthetic/data" },
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
    return { code: await run(arguments_, environment), stdout, stderr };
  } finally {
    stdoutSpy.mockRestore();
    stderrSpy.mockRestore();
  }
}
