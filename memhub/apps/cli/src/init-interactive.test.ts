// A2 / A19 (2026-09-29): `init` must be dual-mode.
//
// The product decision is explicit: on a TTY `init` walks a human through
// picking sources, setting the read/write switches, and choosing whether to
// import; off a TTY it must never prompt or block, and `--all-detected` must
// import every source the Runtime actually found in a single batch call.

import { mkdtemp, readdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { run } from "./cli.js";
import type { WizardPrompter } from "./init-wizard.js";

const transport = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
  request: vi.fn(),
}));

vi.mock("axios", () => ({
  default: { create: vi.fn(() => transport), get: vi.fn() },
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

const BACKGROUND_HEALTHY = {
  state: "healthy" as const,
  code: "background_verified",
  core: "running" as const,
  broker: "running" as const,
  instanceId: "instance-1",
};

beforeEach(async () => {
  vi.clearAllMocks();
  runtime.inspectRuntimeStatus.mockResolvedValue(RUNNING);
  runtime.discoverRuntimeAttachment.mockResolvedValue(ATTACHED);
  runtime.inspectBackgroundHealth.mockResolvedValue(BACKGROUND_HEALTHY);
  // Each wizard test gets its own Runtime data dir; the fence now lives under
  // that dir, keyed by operation id.
  dataDir = await mkdtemp(path.join(tmpdir(), "memhub-init-"));
});

let dataDir = "";

function candidate(source_id: string, display_name: string) {
  return {
    source_id,
    display_name,
    storage_kinds: ["history"],
    available: true,
    authorized: false,
    detected: true,
    root_label: display_name,
    candidate_path: `/synthetic/${source_id}`,
    candidate_kind: "default",
    imported_conversations: 0,
    imported_messages: 0,
    capabilities: {
      historical_import: "implemented",
      incremental_import: "implemented",
      live_sync: "implemented",
      recall: "implemented",
      installer: "implemented",
      verification: "verified",
    },
    sync_completeness: "additions_only",
    location_state: "found",
    candidate_root_id: `root:v2:${"a".repeat(64)}`,
    candidate_revision: 0,
    requires_override_confirmation: false,
    grant_revision: 0,
    root_id: null,
    sync_enabled: false,
  };
}

function captureStdout() {
  let value = "";
  const original = process.stdout.write;
  process.stdout.write = ((chunk: string) => {
    value += chunk;
    return true;
  }) as typeof process.stdout.write;
  return {
    value: () => value,
    restore: () => (process.stdout.write = original),
  };
}

/** A complete ImportJob, so `validateSourceReceipt` accepts the post response. */
function job(source_id: string, job_id: string, stored: number) {
  return {
    job_id,
    user_id: "local",
    source_id,
    operation: "history",
    status: "pending",
    phase: null,
    checkpoint: null,
    discovered: stored,
    discovered_targets: stored,
    emitted: stored,
    stored,
    revised: 0,
    merged: 0,
    skipped: 0,
    errors: 0,
    created_at: "2026-09-29T00:00:00Z",
    updated_at: "2026-09-29T00:00:00Z",
  };
}

function scriptedPrompter(
  lines: string[],
): WizardPrompter & { asked: number; written: string[] } {
  let index = 0;
  const prompter = {
    asked: 0,
    written: [] as string[],
    async ask() {
      prompter.asked += 1;
      const line = lines[index];
      index += 1;
      if (line === undefined) throw new Error("script exhausted");
      return line;
    },
    write(line: string) {
      prompter.written.push(line);
    },
  };
  return prompter;
}

function extendedPrompter(lines: string[], secret: string) {
  const base = scriptedPrompter(lines);
  return {
    ...base,
    async askSecret() {
      return secret;
    },
  };
}

describe("A2 dual-mode init", () => {
  it("never prompts and prints the candidate list when stdin is not a TTY", async () => {
    const get = transport.get.mockImplementation(async (url: string) =>
      url === "/api/v1/sources"
        ? { status: 200, data: [candidate("codex", "Codex")] }
        : { status: 404, data: { detail: "not_found" } },
    );
    const prompter = scriptedPrompter([]);
    const streams = captureStdout();
    try {
      // No --source, stdin not a TTY: unchanged behaviour, no read of stdin.
      const code = await run(
        ["init"],
        {},
        {
          stdinIsTTY: false,
          wizardPrompter: prompter,
        },
      );
      expect(code).toBe(0);
    } finally {
      streams.restore();
    }
    expect(prompter.asked).toBe(0);
    expect(streams.value()).toContain("可接入的来源");
    expect(streams.value()).toContain("codex");
    expect(get).toHaveBeenCalled();
  });

  it("drives the three-step wizard on a TTY and connects + imports the picks", async () => {
    transport.get.mockImplementation(async (url: string) =>
      url === "/api/v1/sources"
        ? {
            status: 200,
            data: [candidate("codex", "Codex"), candidate("cursor", "Cursor")],
          }
        : url.startsWith("/api/v1/source-connections/")
          ? { status: 404, data: { detail: "not_found" } }
          : { status: 404, data: { detail: "not_found" } },
    );
    transport.post.mockImplementation(
      async (_url: string, body: { request_id: string }) => ({
        status: 200,
        data: {
          request_id: body.request_id,
          accepted_source_ids: ["codex", "cursor"],
          failed_sources: {},
          jobs: [job("codex", "j1", 1), job("cursor", "j2", 1)],
        },
      }),
    );
    // "" -> select all; "" -> keep read/write on; "" -> import history.
    const prompter = scriptedPrompter(["", "", ""]);
    const streams = captureStdout();
    try {
      const code = await run(
        ["init"],
        { ONE_MEMORY_DATA_DIR: dataDir },
        {
          stdinIsTTY: true,
          wizardPrompter: prompter,
        },
      );
      expect(code).toBe(0);
    } finally {
      streams.restore();
    }
    expect(prompter.asked).toBeGreaterThanOrEqual(2);
    // One connect call, both sources in it.
    const connectCalls = transport.post.mock.calls.filter(
      (call: unknown[]) => call[0] === "/api/v1/sources/connect",
    );
    expect(connectCalls).toHaveLength(1);
    const body = connectCalls[0]![1] as { source_ids: string[] };
    expect(body.source_ids).toEqual(["codex", "cursor"]);
    expect(streams.value()).toContain("已接入来源：codex、cursor");
    expect(streams.value()).toContain("处理：已开启");
  });

  it("continues the TTY wizard through model setup and probe", async () => {
    const profileId = "22222222-2222-4222-8222-222222222222";
    const profile = {
      profile_id: profileId,
      name: "DeepSeek",
      provider_kind: "openai_compatible",
      base_url: "https://api.deepseek.com",
      model: "deepseek-flash",
      embedding_model: "bge-small-zh-v1.5",
      embedding_provider_kind: "local_onnx",
      embedding_base_url: null,
      embedding_dimensions: 512,
      has_api_key: true,
      credential_state: "available",
      timeout_seconds: 90,
      max_concurrency: 4,
      context_window: null,
      disable_thinking: true,
      capabilities: {
        endpoint_reachable: true,
        model_available: true,
        structured_output: true,
        embedding_available: true,
      },
      revision: 1,
      is_active: true,
      created_at: "2026-10-07T00:00:00Z",
      updated_at: "2026-10-07T00:00:00Z",
    };
    transport.get.mockImplementation(async (url: string) => {
      if (url === "/api/v1/sources") {
        return { status: 200, data: [candidate("codex", "Codex")] };
      }
      if (url === "/api/v1/model-profiles") {
        return { status: 200, data: [profile] };
      }
      if (url.startsWith("/api/v1/source-connections/")) {
        return { status: 404, data: { detail: "not_found" } };
      }
      if (url.startsWith("/api/v1/model-probe-operations/")) {
        return {
          status: 200,
          data: {
            schema_version: 1,
            operation_id: url.split("/").at(-1),
            profile_id: null,
            expected_profile_revision: null,
            state: "not_found",
            result: null,
            code: null,
            stale: null,
          },
        };
      }
      return { status: 404, data: { detail: "not_found" } };
    });
    transport.post.mockImplementation(
      async (url: string, body: Record<string, unknown>) => {
        if (url === "/api/v1/sources/connect") {
          return {
            status: 200,
            data: {
              request_id: body.request_id,
              accepted_source_ids: ["codex"],
              failed_sources: {},
              jobs: [job("codex", "j1", 1)],
            },
          };
        }
        if (url === "/api/v1/model-profiles") {
          return { status: 200, data: { ...profile, is_active: false } };
        }
        if (url.endsWith("/probe")) {
          return {
            status: 200,
            data: {
              schema_version: 1,
              operation_id: body.operation_id,
              profile_id: profileId,
              expected_profile_revision: 1,
              state: "completed",
              result: {
                profile_id: profileId,
                profile_revision: 1,
                input_profile_revision: 1,
                stages: [
                  {
                    stage: "model",
                    ok: true,
                    code: "ok",
                    latency_ms: 1,
                    embedding_dimensions: null,
                  },
                ],
                capabilities: profile.capabilities,
              },
              code: null,
              stale: false,
            },
          };
        }
        if (url.endsWith("/activate")) return { status: 200, data: profile };
        if (url === "/api/v1/onboarding/complete") {
          return {
            status: 200,
            data: {
              status: "completed",
              selected_source_ids: ["codex"],
              started_at: "2026-10-07T00:00:00Z",
              completed_at: "2026-10-07T00:00:01Z",
              updated_at: "2026-10-07T00:00:01Z",
            },
          };
        }
        return { status: 404, data: { detail: "not_found" } };
      },
    );
    const prompt = extendedPrompter(
      ["", "", "", "", "", "", "", "n"],
      "sk-never-print-this",
    );
    const streams = captureStdout();
    try {
      expect(
        await run(
          ["init"],
          {
            ONE_MEMORY_DATA_DIR: dataDir,
            ONE_MEMORY_HOST_HOME: dataDir,
          },
          { stdinIsTTY: true, wizardPrompter: prompt },
        ),
      ).toBe(0);
    } finally {
      streams.restore();
    }
    expect(streams.value()).toContain("已接入来源：codex");
    expect(streams.value()).toContain("首次设置完成。");
    expect(`${streams.value()}\n${prompt.written.join("\n")}`).not.toContain(
      "sk-never-print-this",
    );
    expect(transport.post).toHaveBeenCalledWith(
      "/api/v1/model-profiles",
      expect.objectContaining({ api_key: "sk-never-print-this" }),
      undefined,
    );
    expect(transport.post).not.toHaveBeenCalledWith(
      "/api/v1/model-content-permission",
      expect.anything(),
      expect.anything(),
    );
    expect(transport.post).toHaveBeenCalledWith(
      "/api/v1/onboarding/complete",
      {},
      undefined,
    );
  });

  // QA-CLI R3 F1: a stale CWD `init.one-memory-attempt` used to poison every
  // later wizard run. The fence now lives under the isolated data dir and is
  // keyed by the operation id.
  it("ignores a stale CWD init fence and keeps its fence under the data dir", async () => {
    transport.get.mockImplementation(async (url: string) =>
      url === "/api/v1/sources"
        ? { status: 200, data: [candidate("codex", "Codex")] }
        : { status: 404, data: { detail: "not_found" } },
    );
    transport.post.mockImplementation(
      async (_url: string, body: { request_id: string }) => ({
        status: 200,
        data: {
          request_id: body.request_id,
          accepted_source_ids: ["codex"],
          failed_sources: {},
          jobs: [job("codex", "j1", 1)],
        },
      }),
    );
    const dataDir = await mkdtemp(path.join(tmpdir(), "memhub-init-data-"));
    const staleCwdFence = path.join(
      await mkdtemp(path.join(tmpdir(), "memhub-init-cwd-")),
      "init.one-memory-attempt",
    );
    await writeFile(
      staleCwdFence,
      `${JSON.stringify({
        schema_version: 1,
        operation_id: "stale-operation",
        plan_digest: "a".repeat(64),
      })}\n`,
      { mode: 0o600 },
    );
    const prompter = scriptedPrompter(["", "", ""]);
    const streams = captureStdout();
    let code = -1;
    try {
      code = await run(
        ["init"],
        { ONE_MEMORY_DATA_DIR: dataDir },
        {
          stdinIsTTY: true,
          wizardPrompter: prompter,
        },
      );
    } finally {
      streams.restore();
    }
    expect(code).toBe(0);
    expect(streams.value()).toContain("已接入来源：codex");
    const dataEntries = await readdir(dataDir);
    expect(
      dataEntries.some((entry) => entry.startsWith(".cli-init-attempt-")),
    ).toBe(true);
  });

  it("keeps selected Harnesses frozen without creating history jobs", async () => {
    transport.get.mockImplementation(async (url: string) =>
      url === "/api/v1/sources"
        ? { status: 200, data: [candidate("codex", "Codex")] }
        : { status: 404, data: { detail: "not_found" } },
    );
    transport.post.mockImplementation(async () => ({
      status: 200,
      data: { source_id: "codex", sync_enabled: false },
    }));
    const prompter = scriptedPrompter(["", "n"]);
    const streams = captureStdout();
    try {
      await run(
        ["init"],
        { ONE_MEMORY_DATA_DIR: dataDir },
        {
          stdinIsTTY: true,
          wizardPrompter: prompter,
        },
      );
    } finally {
      streams.restore();
    }
    const connectCalls = transport.post.mock.calls.filter(
      (call: unknown[]) => call[0] === "/api/v1/sources/connect",
    );
    expect(connectCalls).toHaveLength(0);
    expect(streams.value()).toContain("处理：已冻结");
  });
});

describe("A19 init --all-detected", () => {
  it("connects every found source in one batch call without prompting", async () => {
    transport.get.mockImplementation(async (url: string) =>
      url === "/api/v1/sources"
        ? {
            status: 200,
            data: [
              candidate("codex", "Codex"),
              candidate("cursor", "Cursor"),
              {
                ...candidate("claude", "Claude"),
                location_state: "not_found",
                available: false,
                detected: false,
                candidate_root_id: null,
              },
            ],
          }
        : { status: 404, data: { detail: "not_found" } },
    );
    transport.post.mockImplementation(
      async (_url: string, body: { request_id: string }) => ({
        status: 200,
        data: {
          request_id: body.request_id,
          accepted_source_ids: ["codex", "cursor"],
          failed_sources: {},
          jobs: [job("codex", "j1", 2), job("cursor", "j2", 4)],
        },
      }),
    );
    const prompter = scriptedPrompter([]);
    const streams = captureStdout();
    let code = 1;
    try {
      code = await run(
        ["init", "--all-detected", "--import", "--json"],
        {},
        {
          stdinIsTTY: true,
          wizardPrompter: prompter,
        },
      );
    } finally {
      streams.restore();
    }
    // --json forces the non-interactive path even on a TTY.
    expect(prompter.asked).toBe(0);
    expect(code).toBe(0);
    const document = JSON.parse(streams.value());
    expect(document.command).toBe("init");
    expect(document.state).toBe("committed");
    expect(
      document.sources.map((row: { source_id: string }) => row.source_id),
    ).toEqual(["codex", "cursor", "claude"]);
    // not_found is listed, but not connected.
    const connectCalls = transport.post.mock.calls.filter(
      (call: unknown[]) => call[0] === "/api/v1/sources/connect",
    );
    expect(connectCalls).toHaveLength(1);
    const body = connectCalls[0]![1] as { source_ids: string[] };
    expect(body.source_ids).toEqual(["codex", "cursor"]);
  });

  it("reports partial success and exits non-zero", async () => {
    transport.get.mockImplementation(async (url: string) =>
      url === "/api/v1/sources"
        ? {
            status: 200,
            data: [candidate("codex", "Codex"), candidate("cursor", "Cursor")],
          }
        : { status: 404, data: { detail: "not_found" } },
    );
    transport.post.mockImplementation(
      async (_url: string, body: { request_id: string }) => ({
        status: 200,
        data: {
          request_id: body.request_id,
          accepted_source_ids: ["codex"],
          failed_sources: { cursor: "source_identity_changed" },
          jobs: [job("codex", "j1", 1)],
        },
      }),
    );
    const streams = captureStdout();
    let code = 0;
    try {
      code = await run(["init", "--all-detected", "--import", "--json"], {});
    } finally {
      streams.restore();
    }
    // Partial success is not "all good": the script must be able to tell.
    expect(code).toBe(1);
    const document = JSON.parse(streams.value());
    expect(document.state).toBe("partial");
    const cursorRow = document.sources.find(
      (row: { source_id: string }) => row.source_id === "cursor",
    );
    expect(cursorRow.failure_reason).toBe("source_identity_changed");
  });
});
