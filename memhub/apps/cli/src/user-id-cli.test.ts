import { mkdtemp, writeFile } from "node:fs/promises";
import os, { tmpdir } from "node:os";
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
const direct = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("axios", () => ({
  default: {
    create: vi.fn(() => transport),
    get: direct.get,
    post: direct.post,
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

const REQUEST = "33333333-3333-4333-8333-333333333333";
const HERMES = {
  source_id: "hermes",
  display_name: "Hermes",
  storage_kinds: ["sqlite"],
  available: true,
  authorized: true,
  detected: null,
  root_label: ".hermes",
  candidate_path: "~/.hermes",
  candidate_kind: "default",
  candidate_revision: 0,
  candidate_root_id: "hermes-root-identity",
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
const CODEX = {
  ...HERMES,
  source_id: "codex",
  display_name: "Codex",
  candidate_root_id: "codex-root-identity",
};

function addReceipt() {
  return {
    evidence_id: "44444444-4444-4444-8444-444444444444",
    text: "Prefers tea",
    scope: "all-apps",
    created_at: "2026-10-27T00:00:00Z",
    state: "pending",
    task_id: null,
    memory_ids: [],
    error: null,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  for (const method of [
    ...Object.values(transport),
    ...Object.values(direct),
  ]) {
    method.mockReset();
  }
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
  transport.get.mockImplementation(async (url: string) => {
    if (url === "/api/v1/sources") {
      return { status: 200, data: [HERMES, CODEX] };
    }
    if (url === "/api/v2/user-profile") {
      return {
        status: 200,
        data: { request_id: "r-1", profile: null, elapsed_ms: 1 },
      };
    }
    return { status: 404, data: {} };
  });
  transport.post.mockImplementation(
    async (url: string, body: Record<string, unknown>) =>
      url === "/api/v1/sources/connect"
        ? {
            status: 200,
            data: {
              request_id: body.request_id,
              accepted_source_ids: ["hermes"],
              failed_sources: {},
              jobs: [],
            },
          }
        : url === "/api/v2/memories"
          ? { status: 201, data: addReceipt() }
          : { status: 404, data: {} },
  );
  transport.put.mockResolvedValue({ status: 200, data: {} });
  transport.delete.mockResolvedValue({ status: 204, data: "" });
  direct.post.mockResolvedValue({
    status: 202,
    data: {
      request_id: REQUEST,
      state: "accepted",
      created_at: "T1",
      updated_at: "T1",
    },
  });
  direct.get.mockResolvedValue({
    data: {
      request_id: REQUEST,
      state: "accepted",
      created_at: "T1",
      updated_at: "T1",
    },
  });
});

describe("--user-id on the memory commands", () => {
  it("passes the user as a query parameter to memory reads and deletes", async () => {
    await capturedRun(["memories", "list", "--user-id", "wife", "--json"]);
    expect(transport.get).toHaveBeenCalledWith("/api/v2/memory-library", {
      params: { offset: 0, limit: 100, user_id: "wife" },
    });
    // Global: the flag may come before the subcommand too.
    await capturedRun(["memories", "--user-id", "wife", "show", "m-1"]);
    expect(transport.get).toHaveBeenCalledWith("/api/v2/memories/m-1", {
      params: { user_id: "wife" },
    });
    await capturedRun(["memories", "add-status", REQUEST, "--user-id", "wife"]);
    expect(transport.get).toHaveBeenCalledWith(
      `/api/v2/memories/additions/${REQUEST}`,
      { params: { user_id: "wife" } },
    );
    await capturedRun(["profile", "--user-id", "wife", "--json"]);
    expect(transport.get).toHaveBeenCalledWith("/api/v2/user-profile", {
      params: { user_id: "wife" },
    });
    await capturedRun([
      "memories",
      "delete",
      "fact-1",
      "--kind",
      "fact",
      "--yes",
      "--user-id",
      "wife",
    ]);
    expect(transport.delete).toHaveBeenCalledWith(
      "/api/v2/memories/fact-1?kind=fact&user_id=wife",
      undefined,
    );
    await capturedRun([
      "memories",
      "delete",
      "m-1",
      "--yes",
      "--user-id",
      "wife",
    ]);
    expect(transport.delete).toHaveBeenCalledWith(
      "/api/v2/memories/m-1?user_id=wife",
      undefined,
    );
    await capturedRun(["write", "status", REQUEST, "--user-id", "wife"]);
    expect(direct.get.mock.calls[0][0]).toBe(
      `http://127.0.0.1:8765/api/v2/memory-writes/${REQUEST}`,
    );
    expect(direct.get.mock.calls[0][1].params).toEqual({ user_id: "wife" });
  });

  it("prints the retry code and time of a write still waiting", async () => {
    direct.get.mockResolvedValue({
      data: {
        request_id: REQUEST,
        state: "accepted",
        created_at: "T1",
        updated_at: "T2",
        error_code: "model_timeout",
        next_retry_at: "2026-11-02T10:00:00+00:00",
      },
    });
    const text = await capturedRun(["write", "status", REQUEST]);
    expect(text.stdout).toContain("error_code: model_timeout");
    expect(text.stdout).toContain("next_retry_at: 2026-11-02T10:00:00+00:00");
    const json = await capturedRun(["write", "status", REQUEST, "--json"]);
    expect(JSON.parse(json.stdout).write).toMatchObject({
      error_code: "model_timeout",
      next_retry_at: "2026-11-02T10:00:00+00:00",
    });
  });

  it("passes the user in the body of memory writes and searches", async () => {
    await capturedRun(["memories", "add", "Prefers tea", "--user-id", "wife"]);
    expect(transport.post.mock.calls[0][0]).toBe("/api/v2/memories");
    expect(transport.post.mock.calls[0][1]).toMatchObject({
      text: "Prefers tea",
      scope: "all-apps",
      user_id: "wife",
    });
    const directory = await mkdtemp(path.join(tmpdir(), "om-user-id-"));
    const content = path.join(directory, "content.txt");
    await writeFile(content, "Prefers green tea\n");
    await capturedRun([
      "memories",
      "update",
      "m-1",
      "--content-file",
      content,
      "--user-id",
      "wife",
    ]);
    expect(transport.put.mock.calls[0][0]).toBe("/api/v2/memories/m-1");
    expect(transport.put.mock.calls[0][1]).toMatchObject({ user_id: "wife" });
    await capturedRun(["recall", "tea", "--user-id", "wife", "--json"]);
    const search = transport.post.mock.calls.find(
      ([url]) => url === "/api/v2/context/search",
    );
    expect(search?.[1]).toMatchObject({ query: "tea", user_id: "wife" });
    const messages = path.join(directory, "messages.json");
    await writeFile(
      messages,
      JSON.stringify([{ role: "user", content: "I like tea" }]),
    );
    await capturedRun([
      "write",
      "submit",
      "--messages-file",
      messages,
      "--session",
      "s-1",
      "--user-id",
      "wife",
    ]);
    expect(direct.post.mock.calls[0][0]).toBe(
      "http://127.0.0.1:8765/api/v2/memory-writes",
    );
    expect(direct.post.mock.calls[0][1]).toMatchObject({
      session_id: "s-1",
      user_id: "wife",
    });
  });

  it("names no user by default or for --user-id local", async () => {
    for (const extra of [[], ["--user-id", "local"]]) {
      transport.get.mockClear();
      transport.post.mockClear();
      await capturedRun(["memories", "list", ...extra, "--json"]);
      expect(transport.get).toHaveBeenCalledWith("/api/v2/memory-library", {
        params: { offset: 0, limit: 100 },
      });
      await capturedRun(["profile", ...extra]);
      expect(transport.get).toHaveBeenCalledWith(
        "/api/v2/user-profile",
        undefined,
      );
      await capturedRun(["memories", "add", "Prefers tea", ...extra]);
      expect(transport.post.mock.calls[0][1]).not.toHaveProperty("user_id");
      await capturedRun(["recall", "tea", ...extra, "--json"]);
      const search = transport.post.mock.calls.find(
        ([url]) => url === "/api/v2/context/search",
      );
      expect(search?.[1]).not.toHaveProperty("user_id");
    }
  });

  it("refuses a malformed user id before contacting Core", async () => {
    for (const value of ["bad:id", "", "x".repeat(65), "a b", "--json"]) {
      const result = await capturedRun([
        "memories",
        "list",
        "--user-id",
        value,
      ]);
      expect(result.code).toBe(2);
    }
    expect(
      (await capturedRun(["profile", "--user-id", "a", "--user-id", "b"])).code,
    ).toBe(2);
    expect((await capturedRun(["recall", "tea", "--user-id"])).code).toBe(2);
    expect(transport.get).not.toHaveBeenCalled();
    expect(transport.post).not.toHaveBeenCalled();
  });
});

describe("sources connect for a Hermes profile", () => {
  it("connects hermes for another user from its own root", async () => {
    const result = await capturedRun([
      "sources",
      "connect",
      "hermes",
      "--user-id",
      "wife",
      "--root",
      "~/.hermes/profiles/wife",
      "--json",
    ]);
    expect(result.code).toBe(0);
    expect(transport.post).toHaveBeenCalledTimes(1);
    const [url, body] = transport.post.mock.calls[0];
    expect(url).toBe("/api/v1/sources/connect");
    expect(body).toMatchObject({
      source_ids: ["hermes"],
      // Core reads the custom root's identity itself.
      source_roots: {},
      source_candidate_revisions: {},
      onboarding: false,
      user_id: "wife",
      root: path.join(os.homedir(), ".hermes/profiles/wife"),
    });
  });

  it("keeps a plain connect unchanged and accepts --import with a profile", async () => {
    await capturedRun(["sources", "connect", "hermes", "--import", "--json"]);
    const plain = transport.post.mock.calls[0][1];
    expect(plain).not.toHaveProperty("user_id");
    expect(plain).not.toHaveProperty("root");
    expect(plain).toMatchObject({
      source_roots: { hermes: "hermes-root-identity" },
      onboarding: true,
    });
    transport.post.mockClear();
    const result = await capturedRun([
      "sources",
      "connect",
      "hermes",
      "--import",
      "--user-id",
      "wife",
      "--root",
      "/profiles/wife",
      "--request-id",
      REQUEST,
      "--json",
    ]);
    expect(result.code).toBe(0);
    expect(transport.post.mock.calls[0][1]).toMatchObject({
      request_id: REQUEST,
      user_id: "wife",
      root: "/profiles/wife",
    });
  });

  it("refuses --user-id and --root for any source but hermes", async () => {
    for (const argv of [
      ["sources", "connect", "codex", "--user-id", "wife"],
      ["sources", "connect", "codex", "--root", "/somewhere", "--import"],
      ["sources", "connect", "hermes", "codex", "--user-id", "wife"],
      ["sources", "connect", "--all-detected", "--user-id", "wife"],
      ["sources", "connect", "hermes", "--user-id", "bad:id"],
    ]) {
      const result = await capturedRun(argv);
      expect(result.code).toBe(2);
      expect(result.stderr).toContain("hermes");
    }
    expect(transport.post).not.toHaveBeenCalled();
  });

  it("says the user id need not equal the Hermes profile name", async () => {
    const result = await capturedRun([
      "sources",
      "connect",
      "hermes",
      "--user-id",
      "wife:work",
      "--root",
      "~/.hermes/profiles/wife:work",
    ]);
    expect(result.code).toBe(2);
    expect(result.stderr).toContain("用户 ID 不必等于 Hermes profile 名");
    expect(result.stderr).toContain(
      "--user-id wife-work --root ~/.hermes/profiles/wife:work",
    );
    expect(transport.post).not.toHaveBeenCalled();
  });

  it("explains a refused overlapping or profiles-container root", async () => {
    for (const code of [
      "hermes_root_is_profiles_container",
      "hermes_root_overlaps_other_user",
    ]) {
      transport.post.mockResolvedValueOnce({
        status: code.endsWith("container") ? 422 : 409,
        data: { detail: code },
      });
      const result = await capturedRun([
        "sources",
        "connect",
        "hermes",
        "--user-id",
        "kid",
        "--root",
        "/profiles",
      ]);
      expect(result.code).toBe(1);
      expect(result.stdout).toContain(code);
      expect(result.stdout).toContain("--root");
    }
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
