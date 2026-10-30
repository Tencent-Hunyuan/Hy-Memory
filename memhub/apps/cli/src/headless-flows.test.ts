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
  authorized: false,
  detected: null,
  root_label: "Codex history",
  candidate_path: "/SYNTHETIC_PRIVATE_PATH",
  candidate_kind: "default",
  candidate_revision: 0,
  candidate_root_id: "root-identity-1",
  imported_messages: 0,
  imported_conversations: 0,
  capabilities: {
    historical_import: "implemented",
    incremental_import: "implemented",
    live_sync: "implemented",
    recall: "implemented",
    installer: "not_implemented",
    verification: "fixture_only",
  },
  sync_completeness: "additions_only",
  location_state: "found",
};
const MISSING_SOURCE = {
  ...SOURCE,
  source_id: "workbuddy",
  display_name: "WorkBuddy",
  candidate_path: "/SYNTHETIC_MISSING_PATH",
  candidate_root_id: null,
  location_state: "not_found",
};

const PROFILE = {
  profile_id: "profile-1",
  name: "Synthetic",
  provider_kind: "openai_compatible",
  base_url: "https://example.invalid/v1",
  model: "synthetic-model",
  embedding_model: null,
  embedding_provider_kind: null,
  embedding_base_url: null,
  embedding_dimensions: null,
  has_api_key: true,
  credential_state: "available",
  timeout_seconds: 240,
  max_concurrency: 4,
  context_window: null,
  disable_thinking: true,
  capabilities: {
    endpoint_reachable: false,
    model_available: false,
    structured_output: false,
    embedding_available: false,
  },
  revision: 1,
  is_active: false,
  created_at: "T1",
  updated_at: "T1",
};
const PROBE_PROFILE = {
  ...PROFILE,
  profile_id: "22222222-2222-4222-8222-222222222222",
  revision: 3,
};

let directory: string;

beforeEach(async () => {
  vi.clearAllMocks();
  directory = await mkdtemp(path.join(tmpdir(), "om-headless-"));
  runtime.inspectRuntimeStatus.mockResolvedValue(RUNNING);
  runtime.discoverRuntimeAttachment.mockResolvedValue(ATTACHED);
  transport.get.mockImplementation(async (url: string) => {
    if (url === "/api/v1/sources") {
      return { status: 200, data: [SOURCE, MISSING_SOURCE] };
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
            jobs: [importJob()],
          },
        };
      }
      if (url === "/api/v1/model-profiles") {
        return { status: 200, data: PROFILE };
      }
      return { status: 404, data: {} };
    },
  );
});

const CONNECT = ["sources", "connect", "codex", "--import", "--json"];

function connectReceipt(
  accepted: string[],
  failed: Record<string, string>,
  jobs: unknown[],
) {
  return async (_url: string, body: Record<string, unknown>) => ({
    status: 200,
    data: {
      request_id: body.request_id,
      accepted_source_ids: accepted,
      failed_sources: failed,
      jobs,
    },
  });
}

describe("sources connect --import", () => {
  it("connects and imports in one direct request and returns the request id", async () => {
    const result = await capturedRun(CONNECT);
    expect(result.code).toBe(0);
    const document = JSON.parse(result.stdout);
    expect(document.state).toBe("committed");
    expect(document.operation_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(transport.post).toHaveBeenCalledTimes(1);
    expect(transport.post).toHaveBeenCalledWith(
      "/api/v1/sources/connect",
      expect.objectContaining({
        request_id: document.operation_id,
        source_ids: ["codex"],
        source_roots: { codex: "root-identity-1" },
        confirm_environment_overrides: false,
        full_rescan: false,
        onboarding: true,
        enable_sync: true,
      }),
      undefined,
    );
    expect(result.stdout).not.toContain("SYNTHETIC_PRIVATE_PATH");
  });

  it("uses a caller-supplied --request-id as the idempotency key", async () => {
    const requestId = "33333333-3333-4333-8333-333333333333";
    const result = await capturedRun([...CONNECT, "--request-id", requestId]);
    expect(result.code).toBe(0);
    expect(transport.post.mock.calls[0][1]).toMatchObject({
      request_id: requestId,
    });
    expect(
      (await capturedRun([...CONNECT, "--request-id", "not-a-uuid"])).code,
    ).toBe(2);
    // A request id only means something for an import.
    expect(
      (
        await capturedRun([
          "sources",
          "connect",
          "codex",
          "--request-id",
          requestId,
        ])
      ).code,
    ).toBe(2);
  });

  it("refuses an environment-override source without --confirm-overrides", async () => {
    transport.get.mockImplementation(async (url: string) =>
      url === "/api/v1/sources"
        ? {
            status: 200,
            data: [{ ...SOURCE, candidate_kind: "environment_override" }],
          }
        : { status: 404, data: {} },
    );
    const refused = await capturedRun(CONNECT);
    expect(refused.code).toBe(1);
    expect(JSON.parse(refused.stdout)).toMatchObject({
      state: "rejected",
      code: "environment_override_confirmation_required",
      receipt: { override_required_source_ids: ["codex"] },
    });
    expect(transport.post).not.toHaveBeenCalled();

    const confirmed = await capturedRun([...CONNECT, "--confirm-overrides"]);
    expect(confirmed.code).toBe(0);
    expect(transport.post.mock.calls[0][1]).toMatchObject({
      confirm_environment_overrides: true,
    });
  });

  // C8 P2 (2026-09-29 H18r2): when every source was rejected, the receipt had
  // `accepted_source_ids: []`, yet the CLI still reported `committed`.
  it("reports all-rejected as rejected with a non-zero exit and the reason, in JSON and text", async () => {
    transport.post.mockImplementation(
      connectReceipt([], { codex: "source_read_disabled" }, []),
    );
    const result = await capturedRun(CONNECT);
    expect(result.code).toBe(1);
    const document = JSON.parse(result.stdout);
    expect(document.state).toBe("rejected");
    expect(document.code).toBe("source_import_all_rejected");
    expect(document.receipt.failed_sources).toEqual({
      codex: "source_read_disabled",
    });
    const text = await capturedRun(CONNECT.slice(0, -1));
    expect(text.code).toBe(1);
    expect(text.stdout).toContain("source_read_disabled");
    expect(text.stdout).not.toContain("操作状态：committed");
  });

  it("reports partial success with a non-zero exit and keeps the receipt", async () => {
    transport.post.mockImplementation(
      connectReceipt(["codex"], { cursor: "source_identity_changed" }, [
        importJob(),
      ]),
    );
    const result = await capturedRun(CONNECT);
    expect(result.code).toBe(1);
    const document = JSON.parse(result.stdout);
    expect(document.state).toBe("partial");
    expect(document.receipt.accepted_source_ids).toEqual(["codex"]);
  });

  it("rejects a fresh all-busy receipt as source_import_all_busy", async () => {
    transport.post.mockImplementation(
      connectReceipt([], { codex: "source_import_busy" }, []),
    );
    const result = await capturedRun(CONNECT);
    expect(result.code).toBe(1);
    expect(JSON.parse(result.stdout)).toMatchObject({
      state: "rejected",
      code: "source_import_all_busy",
    });
  });

  it("recovers an unknown write by request id without reposting", async () => {
    const requestId = "44444444-4444-4444-8444-444444444444";
    transport.post.mockRejectedValue(new Error("socket closed"));
    transport.get.mockImplementation(async (url: string) => {
      if (url === "/api/v1/sources") {
        return { status: 200, data: [SOURCE] };
      }
      return url === `/api/v1/source-connections/${requestId}`
        ? {
            status: 200,
            data: {
              request_id: requestId,
              accepted_source_ids: ["codex"],
              failed_sources: {},
              jobs: [importJob()],
            },
          }
        : { status: 404, data: {} };
    });
    const result = await capturedRun([...CONNECT, "--request-id", requestId]);
    expect(result.code).toBe(0);
    expect(JSON.parse(result.stdout).state).toBe("recovered");
    expect(transport.post).toHaveBeenCalledTimes(1);
  });

  it("does not recover a nonterminal or all-rejected receipt", async () => {
    const requestId = "55555555-5555-4555-8555-555555555555";
    transport.post.mockRejectedValue(new Error("socket closed"));
    for (const failed of [{}, { codex: "source_read_disabled" }]) {
      transport.get.mockImplementation(async (url: string) =>
        url === "/api/v1/sources"
          ? { status: 200, data: [SOURCE] }
          : url === `/api/v1/source-connections/${requestId}`
            ? {
                status: 200,
                data: {
                  request_id: requestId,
                  accepted_source_ids: [],
                  failed_sources: failed,
                  jobs: [],
                },
              }
            : { status: 404, data: {} },
      );
      const result = await capturedRun([...CONNECT, "--request-id", requestId]);
      expect(result.code).toBe(1);
      expect(JSON.parse(result.stdout).state).not.toBe("recovered");
    }
  });

  it("reads a receipt by request id", async () => {
    const requestId = "66666666-6666-4666-8666-666666666666";
    transport.get.mockImplementation(async (url: string) =>
      url === `/api/v1/source-connections/${requestId}`
        ? {
            status: 200,
            data: {
              request_id: requestId,
              accepted_source_ids: ["codex"],
              failed_sources: {},
              jobs: [importJob()],
            },
          }
        : { status: 404, data: {} },
    );
    const result = await capturedRun([
      "sources",
      "receipt",
      requestId,
      "--json",
    ]);
    expect(result.code).toBe(0);
    expect(JSON.parse(result.stdout)).toMatchObject({
      state: "committed",
      operation_id: requestId,
    });
    expect(transport.post).not.toHaveBeenCalled();
  });

  it("rejects an unknown source, a repeated source and both selections", async () => {
    expect(
      (await capturedRun(["sources", "connect", "nope", "--import"])).code,
    ).toBe(1);
    expect(transport.post).not.toHaveBeenCalled();
    expect(
      (await capturedRun(["sources", "connect", "codex", "codex"])).code,
    ).toBe(2);
    expect(
      (await capturedRun(["sources", "connect", "codex", "--all-detected"]))
        .code,
    ).toBe(2);
    expect((await capturedRun(["sources", "connect"])).code).toBe(2);
  });
});

describe("sources connect without --import grants only", () => {
  it("authorizes each source and keeps processing frozen", async () => {
    transport.post.mockResolvedValue({
      status: 200,
      data: { source_id: "codex", root_id: "root-identity-1" },
    });
    const result = await capturedRun(["sources", "connect", "codex", "--json"]);
    expect(result.code).toBe(0);
    expect(JSON.parse(result.stdout)).toMatchObject({
      command: "sources connect",
      state: "committed",
      import: false,
      granted_source_ids: ["codex"],
    });
    expect(transport.post).toHaveBeenCalledTimes(1);
    expect(transport.post).toHaveBeenCalledWith(
      "/api/v1/sources/codex/authorize",
      {
        user_confirmed: true,
        expected_root_id: "root-identity-1",
        confirm_environment_override: false,
      },
      undefined,
    );
  });

  it("is what the old `init --source` spelling now runs", async () => {
    transport.post.mockResolvedValue({ status: 200, data: {} });
    const result = await capturedRun(["init", "--source", "codex", "--json"]);
    expect(result.code).toBe(0);
    expect(transport.post.mock.calls[0][0]).toBe(
      "/api/v1/sources/codex/authorize",
    );
    // The old no-op flags are gone rather than silently ignored.
    expect(
      (await capturedRun(["init", "--source", "codex", "--full-rescan"])).code,
    ).toBe(2);
    expect(
      (await capturedRun(["init", "--source", "codex", "--dry-run"])).code,
    ).toBe(2);
  });
});

describe("models add", () => {
  it("creates directly and returns the operation id", async () => {
    const configFile = await modelConfigFile();
    const result = await capturedRun([
      "models",
      "add",
      "--config-file",
      configFile,
      "--json",
    ]);
    expect(result.code).toBe(0);
    const document = JSON.parse(result.stdout);
    expect(document).toMatchObject({
      state: "committed",
      receipt: { profile_id: "profile-1" },
    });
    expect(document.operation_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(transport.post).toHaveBeenCalledWith(
      "/api/v1/model-profiles",
      expect.objectContaining({
        name: "Synthetic",
        creation_operation_id: document.operation_id,
      }),
      undefined,
    );
  });

  it("refuses credentials embedded in the config file before contacting Core", async () => {
    const configFile = path.join(directory, "model.json");
    await writeFile(
      configFile,
      JSON.stringify({
        name: "Synthetic",
        provider_kind: "openai_compatible",
        model: "synthetic-model",
        api_key: "SYNTHETIC_MUST_NOT_ENTER_PLAN",
      }),
    );
    const rejected = await capturedRun([
      "models",
      "add",
      "--config-file",
      configFile,
      "--json",
    ]);
    expect(rejected.code).toBe(1);
    expect(JSON.parse(rejected.stdout).code).toBe("model_config_invalid");
    expect(rejected.stdout).not.toContain("SYNTHETIC_MUST_NOT_ENTER_PLAN");
    expect(transport.post).not.toHaveBeenCalled();
  });

  it("reads the API key from a bounded owner-only file and never returns it", async () => {
    const configFile = await modelConfigFile();
    const keyFile = path.join(directory, "key");
    await writeFile(keyFile, "SYNTHETIC_SECRET_KEY\n", { mode: 0o600 });
    const result = await capturedRun([
      "models",
      "add",
      "--config-file",
      configFile,
      "--api-key-file",
      keyFile,
      "--json",
    ]);
    expect(result.code).toBe(0);
    expect(result.stdout + result.stderr).not.toContain("SYNTHETIC_SECRET_KEY");
    expect(transport.post.mock.calls[0][1]).toMatchObject({
      api_key: "SYNTHETIC_SECRET_KEY",
    });
  });

  it("refuses an API-key file readable by group or others", async () => {
    const configFile = await modelConfigFile();
    const keyFile = path.join(directory, "loose-key");
    await writeFile(keyFile, "SYNTHETIC_LOOSE_KEY\n", { mode: 0o644 });
    const result = await capturedRun([
      "models",
      "add",
      "--config-file",
      configFile,
      "--api-key-file",
      keyFile,
      "--json",
    ]);
    expect(result.code).toBe(2);
    expect(transport.post).not.toHaveBeenCalled();
    expect(result.stdout + result.stderr).not.toContain("SYNTHETIC_LOOSE_KEY");
  });

  it("activates the new profile with --activate", async () => {
    const configFile = await modelConfigFile();
    transport.post.mockImplementation(async (url: string) =>
      url === "/api/v1/model-profiles"
        ? { status: 200, data: PROFILE }
        : url === "/api/v1/model-profiles/profile-1/activate"
          ? { status: 200, data: { ...PROFILE, is_active: true } }
          : { status: 404, data: {} },
    );
    const result = await capturedRun([
      "models",
      "add",
      "--config-file",
      configFile,
      "--activate",
      "--json",
    ]);
    expect(result.code).toBe(0);
    expect(JSON.parse(result.stdout).activation).toEqual({
      state: "committed",
    });
    expect(transport.post.mock.calls.map(([url]) => url)).toEqual([
      "/api/v1/model-profiles",
      "/api/v1/model-profiles/profile-1/activate",
    ]);
  });

  it("recovers an unknown create through its operation receipt", async () => {
    const configFile = await modelConfigFile();
    transport.post.mockRejectedValue(new Error("socket closed"));
    transport.get.mockImplementation(async (url: string) => {
      const match = /^\/api\/v1\/model-profile-creations\/(.+)$/.exec(url);
      return match
        ? {
            status: 200,
            data: {
              operation_id: match[1],
              state: "committed",
              profile_id: "profile-1",
              created_revision: 1,
            },
          }
        : { status: 404, data: {} };
    });
    const result = await capturedRun([
      "models",
      "add",
      "--config-file",
      configFile,
      "--json",
    ]);
    expect(result.code).toBe(0);
    expect(JSON.parse(result.stdout).state).toBe("recovered");
    expect(transport.post).toHaveBeenCalledTimes(1);
  });

  it("no longer accepts the plan-file commands", async () => {
    for (const argv of [
      ["models", "plan", "--config-file", "/tmp/x"],
      ["models", "configure", "--plan-file", "/tmp/x", "--confirm"],
      ["models", "probe-plan", PROBE_PROFILE.profile_id],
      ["models", "probe", "--plan-file", "/tmp/x", "--confirm"],
      ["models", "probe-receipt", PROBE_PROFILE.profile_id],
      ["setup", "plan", "--source", "codex"],
      ["sources", "import", "--plan-file", "/tmp/x", "--confirm"],
    ]) {
      expect((await capturedRun(argv)).code).toBe(2);
    }
    expect(transport.post).not.toHaveBeenCalled();
  });
});

describe("models test and receipt", () => {
  it("lists and shows safe model configuration without endpoint leakage", async () => {
    transport.get.mockResolvedValue({
      status: 200,
      data: [
        {
          ...PROBE_PROFILE,
          base_url: "https://internal.example.invalid/v1",
          embedding_provider_kind: "local_onnx",
          embedding_model: "local-model",
        },
      ],
    });
    const listed = await capturedRun(["models", "list", "--json"]);
    expect(listed.code).toBe(0);
    expect(JSON.parse(listed.stdout)).toMatchObject({
      state: "ok",
      profiles: [
        {
          profile_id: PROBE_PROFILE.profile_id,
          generation_endpoint: "custom",
          embedding_mode: "local",
        },
      ],
    });
    expect(listed.stdout).not.toContain("internal.example.invalid");
  });

  it("submits a probe against the current revision and returns its operation id", async () => {
    let operationId = "";
    transport.get.mockImplementation(async (url: string) => {
      if (url === "/api/v1/model-profiles") {
        return { status: 200, data: [PROBE_PROFILE] };
      }
      const match = /^\/api\/v1\/model-probe-operations\/(.+)$/.exec(url);
      if (match) {
        operationId = match[1];
        return { status: 200, data: probeNotFound(operationId) };
      }
      return { status: 404, data: {} };
    });
    transport.post.mockImplementation(async () => ({
      status: 200,
      data: probePending(operationId),
    }));
    const result = await capturedRun([
      "models",
      "test",
      PROBE_PROFILE.profile_id,
      "--json",
    ]);
    expect(result.code).toBe(0);
    expect(JSON.parse(result.stdout)).toMatchObject({
      state: "pending",
      operation_id: operationId,
    });
    expect(transport.post).toHaveBeenCalledTimes(1);
    expect(transport.post).toHaveBeenCalledWith(
      `/api/v1/model-profiles/${PROBE_PROFILE.profile_id}/probe`,
      { operation_id: operationId, expected_profile_revision: 3 },
      undefined,
    );
  });

  it("does not probe on a Core without probe operations", async () => {
    transport.get.mockImplementation(async (url: string) =>
      url === "/api/v1/model-profiles"
        ? { status: 200, data: [PROBE_PROFILE] }
        : { status: 404, data: {} },
    );
    const result = await capturedRun([
      "models",
      "test",
      PROBE_PROFILE.profile_id,
      "--json",
    ]);
    expect(result.code).toBe(1);
    expect(JSON.parse(result.stdout)).toMatchObject({
      state: "disabled",
      code: "model_probe_operations_unsupported",
    });
    expect(transport.post).not.toHaveBeenCalled();
  });

  it("reads a creation receipt first, then a probe receipt", async () => {
    const creation = "77777777-7777-4777-8777-777777777777";
    const probe = "88888888-8888-4888-8888-888888888888";
    transport.get.mockImplementation(async (url: string) => {
      if (url === `/api/v1/model-profile-creations/${creation}`) {
        return {
          status: 200,
          data: {
            operation_id: creation,
            state: "committed",
            profile_id: "profile-1",
            created_revision: 1,
          },
        };
      }
      if (url === `/api/v1/model-profile-creations/${probe}`) {
        return {
          status: 200,
          data: {
            operation_id: probe,
            state: "not_found",
            profile_id: null,
            created_revision: null,
          },
        };
      }
      if (url === `/api/v1/model-probe-operations/${probe}`) {
        return { status: 200, data: probeReceipt(probe) };
      }
      return { status: 404, data: {} };
    });
    const created = await capturedRun([
      "models",
      "receipt",
      creation,
      "--json",
    ]);
    expect(created.code).toBe(0);
    expect(JSON.parse(created.stdout)).toMatchObject({
      operation: "model_profile_create",
      state: "committed",
    });
    const probed = await capturedRun(["models", "receipt", probe, "--json"]);
    expect(probed.code).toBe(0);
    expect(JSON.parse(probed.stdout)).toMatchObject({
      operation: "model_profile_probe",
      state: "completed",
    });
    const missing = await capturedRun([
      "models",
      "receipt",
      "99999999-9999-4999-8999-999999999999",
      "--json",
    ]);
    expect(missing.code).toBe(1);
    expect(JSON.parse(missing.stdout).state).toBe("not_found");
  });
});

async function modelConfigFile(): Promise<string> {
  const configFile = path.join(directory, "model.json");
  await writeFile(
    configFile,
    JSON.stringify({
      name: "Synthetic",
      provider_kind: "openai_compatible",
      base_url: "https://example.invalid/v1",
      model: "synthetic-model",
    }),
  );
  return configFile;
}

function probePending(operationId: string) {
  return {
    schema_version: 1,
    operation_id: operationId,
    profile_id: PROBE_PROFILE.profile_id,
    expected_profile_revision: 3,
    state: "pending",
    result: null,
    code: null,
    stale: false,
  };
}

function probeNotFound(operationId: string) {
  return {
    schema_version: 1,
    operation_id: operationId,
    profile_id: null,
    expected_profile_revision: null,
    state: "not_found",
    result: null,
    code: null,
    stale: null,
  };
}

function probeReceipt(operationId: string) {
  return {
    schema_version: 1,
    operation_id: operationId,
    profile_id: PROBE_PROFILE.profile_id,
    expected_profile_revision: 3,
    state: "completed",
    result: {
      profile_id: PROBE_PROFILE.profile_id,
      profile_revision: 3,
      input_profile_revision: 3,
      stages: [
        {
          stage: "authentication",
          ok: false,
          code: "missing_api_key",
          latency_ms: 0,
          embedding_dimensions: null,
        },
      ],
      capabilities: {
        endpoint_reachable: false,
        model_available: false,
        structured_output: false,
        embedding_available: false,
      },
    },
    code: null,
    stale: false,
  };
}

function importJob() {
  return {
    job_id: "job-1",
    user_id: "local",
    source_id: "codex",
    operation: "history",
    status: "pending",
    phase: "discover",
    checkpoint: null,
    discovered: 0,
    discovered_targets: 0,
    emitted: 0,
    stored: 0,
    revised: 0,
    merged: 0,
    skipped: 0,
    errors: 0,
    created_at: "T1",
    updated_at: "T1",
  };
}

async function capturedRun(argv: string[]) {
  let stdout = "";
  let stderr = "";
  const originalOut = process.stdout.write;
  const originalError = process.stderr.write;
  process.stdout.write = ((chunk: string) => {
    stdout += chunk;
    return true;
  }) as typeof process.stdout.write;
  process.stderr.write = ((chunk: string) => {
    stderr += chunk;
    return true;
  }) as typeof process.stderr.write;
  try {
    const code = await run(argv, {});
    return { code, stdout, stderr };
  } finally {
    process.stdout.write = originalOut;
    process.stderr.write = originalError;
  }
}
