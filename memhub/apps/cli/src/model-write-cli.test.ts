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

const PROFILE = "44444444-4444-4444-8444-444444444444";

function profileResponse(overrides: Record<string, unknown> = {}) {
  return {
    profile_id: PROFILE,
    name: "Renamed",
    provider_kind: "openai_compatible",
    base_url: "https://example.invalid/v1",
    model: "synthetic-model",
    embedding_model: "synthetic-embedding",
    embedding_provider_kind: "openai_compatible",
    embedding_base_url: "https://example.invalid/v1",
    embedding_dimensions: 8,
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

describe("models activate", () => {
  it("keeps models setup TTY-only and never prompts in JSON mode", async () => {
    let asked = 0;
    const stdout = vi
      .spyOn(process.stdout, "write")
      .mockImplementation(() => true);
    const stderr = vi
      .spyOn(process.stderr, "write")
      .mockImplementation(() => true);
    try {
      expect(
        await run(
          ["models", "setup", "--json"],
          { ONE_MEMORY_DATA_DIR: "/synthetic/data" },
          {
            stdinIsTTY: true,
            wizardPrompter: {
              async ask() {
                asked += 1;
                return "";
              },
              async askSecret() {
                asked += 1;
                return "secret";
              },
              write() {},
            },
          },
        ),
      ).toBe(2);
    } finally {
      stdout.mockRestore();
      stderr.mockRestore();
    }
    expect(asked).toBe(0);
    expect(runtime.inspectRuntimeStatus).not.toHaveBeenCalled();
  });

  it("posts the activate endpoint directly and reports the profile", async () => {
    transport.post.mockResolvedValue({
      status: 200,
      data: profileResponse(),
    });
    const result = await capturedRun(["models", "activate", PROFILE, "--json"]);
    expect(result.code).toBe(0);
    expect(transport.post).toHaveBeenCalledWith(
      `/api/v1/model-profiles/${PROFILE}/activate`,
      {},
      undefined,
    );
    const document = JSON.parse(result.stdout);
    expect(document.state).toBe("committed");
    expect(document.receipt.is_active).toBe(true);
  });

  it("maps a 409 activation refusal to a stable rejected code", async () => {
    transport.post.mockResolvedValue({
      status: 409,
      data: { detail: "model_probe_required" },
    });
    const result = await capturedRun([
      "models",
      "activate",
      PROFILE,
      "--confirm",
      "--json",
    ]);
    expect(result.code).toBe(1);
    expect(JSON.parse(result.stdout)).toMatchObject({
      state: "rejected",
      code: "model_probe_required",
    });
  });

  it("never reports a malformed success body as committed", async () => {
    transport.post.mockResolvedValue({
      status: 200,
      data: { profile_id: "x" },
    });
    const result = await capturedRun([
      "models",
      "activate",
      PROFILE,
      "--confirm",
      "--json",
    ]);
    expect(result.code).toBe(1);
    expect(JSON.parse(result.stdout).state).toBe("unknown");
  });
});

describe("models remove", () => {
  it("previews without --yes and changes nothing", async () => {
    const result = await capturedRun(["models", "remove", PROFILE, "--json"]);
    expect(result.code).toBe(0);
    expect(JSON.parse(result.stdout)).toMatchObject({
      command: "models remove",
      state: "preview",
      target: PROFILE,
      applied: false,
    });
    expect(runtime.inspectRuntimeStatus).not.toHaveBeenCalled();
  });
});

describe("models edit", () => {
  it("sends the exact patch body with the CAS revision directly", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "om-model-edit-"));
    const config = path.join(directory, "patch.json");
    await writeFile(
      config,
      JSON.stringify({
        expected_revision: 4,
        name: "Renamed",
        clear_api_key: true,
      }),
      { mode: 0o600 },
    );
    transport.patch.mockResolvedValue({
      status: 200,
      data: profileResponse(),
    });
    const result = await capturedRun([
      "models",
      "edit",
      PROFILE,
      "--config-file",
      config,
      "--json",
    ]);
    expect(result.code).toBe(0);
    expect(transport.patch).toHaveBeenCalledWith(
      `/api/v1/model-profiles/${PROFILE}`,
      { name: "Renamed", clear_api_key: true, expected_revision: 4 },
      undefined,
    );
    expect(JSON.parse(result.stdout).state).toBe("committed");
  });

  it("reads an api key from a controlled file, never from the plan", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "om-model-edit-"));
    const config = path.join(directory, "patch.json");
    const keyFile = path.join(directory, "key");
    await writeFile(config, JSON.stringify({ expected_revision: 4 }), {
      mode: 0o600,
    });
    await writeFile(keyFile, "sk-synthetic\n", { mode: 0o600 });
    transport.patch.mockResolvedValue({
      status: 200,
      data: profileResponse(),
    });
    const result = await capturedRun([
      "models",
      "edit",
      PROFILE,
      "--config-file",
      config,
      "--confirm",
      "--api-key-file",
      keyFile,
      "--json",
    ]);
    expect(result.code).toBe(0);
    expect(transport.patch).toHaveBeenCalledWith(
      `/api/v1/model-profiles/${PROFILE}`,
      { expected_revision: 4, api_key: "sk-synthetic" },
      undefined,
    );
    // The credential must never appear in the emitted document.
    expect(result.stdout).not.toContain("sk-synthetic");
  });

  it("rejects a patch missing expected_revision without writing", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "om-model-edit-"));
    const config = path.join(directory, "patch.json");
    await writeFile(config, JSON.stringify({ name: "Renamed" }), {
      mode: 0o600,
    });
    const result = await capturedRun([
      "models",
      "edit",
      PROFILE,
      "--config-file",
      config,
      "--confirm",
      "--json",
    ]);
    expect(result.code).toBe(1);
    expect(JSON.parse(result.stdout).state).toBe("invalid");
    expect(transport.patch).not.toHaveBeenCalled();
  });

  it("refuses a key file readable by group or others without writing", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "om-model-edit-"));
    const config = path.join(directory, "patch.json");
    const keyFile = path.join(directory, "loose-key");
    await writeFile(config, JSON.stringify({ expected_revision: 4 }), {
      mode: 0o600,
    });
    // A key readable by group/others is not a secret the user controls:
    // accepting it would silently normalise a leak-prone file.
    await writeFile(keyFile, "sk-loose\n", { mode: 0o644 });
    const result = await capturedRun([
      "models",
      "edit",
      PROFILE,
      "--config-file",
      config,
      "--confirm",
      "--api-key-file",
      keyFile,
      "--json",
    ]);
    expect(result.code).toBe(2);
    expect(transport.patch).not.toHaveBeenCalled();
    expect(result.stdout + result.stderr).not.toContain("sk-loose");
  });

  it("maps a 409 revision conflict to a stable rejected code", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "om-model-edit-"));
    const config = path.join(directory, "patch.json");
    await writeFile(config, JSON.stringify({ expected_revision: 4 }), {
      mode: 0o600,
    });
    transport.patch.mockResolvedValue({
      status: 409,
      data: { detail: "model_profile_conflict" },
    });
    const result = await capturedRun([
      "models",
      "edit",
      PROFILE,
      "--config-file",
      config,
      "--confirm",
      "--json",
    ]);
    expect(result.code).toBe(1);
    expect(JSON.parse(result.stdout)).toMatchObject({
      state: "rejected",
      code: "model_profile_conflict",
    });
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
