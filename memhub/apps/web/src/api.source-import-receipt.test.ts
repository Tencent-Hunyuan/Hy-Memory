// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

const fake = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("axios", () => ({
  default: {
    create: () => ({
      get: fake.get,
      post: fake.post,
      interceptors: { request: { use: vi.fn() } },
    }),
    isAxiosError: () => false,
  },
}));

import {
  changeImportJob,
  createImportJob,
  getHistoryFile,
  getImportIssues,
  getImportJob,
  getImportJobs,
  getLiveSyncHealth,
  getMemoryNoteOperation,
  getMemoryNotes,
  getModelCreationOperation,
  getModelProfiles,
  getSourceImportReceipt,
  getSources,
} from "./api";

const receipt = {
  source_id: "cursor",
  measured_at: "2026-09-13T00:00:00Z",
  messages: 5,
  conversations: 2,
  person_messages: 2,
  agent_messages: 1,
  tool_messages: 1,
  unknown_author_messages: 1,
  unknown_time_messages: 4,
  examples: [],
};

afterEach(() => {
  fake.get.mockReset();
  fake.post.mockReset();
});

const modelProfile = {
  profile_id: "profile-1",
  name: "Profile",
  provider_kind: "openai_compatible" as const,
  base_url: "https://example.test/v1",
  model: "model-1",
  embedding_model: "embed-1",
  embedding_provider_kind: "openai_compatible" as const,
  embedding_base_url: "https://example.test/v1",
  embedding_dimensions: 512,
  has_api_key: true,
  timeout_seconds: 30,
  max_concurrency: 2,
  context_window: 8192,
  disable_thinking: true,
  capabilities: {
    endpoint_reachable: true,
    model_available: true,
    structured_output: true,
    embedding_available: true,
  },
  revision: 1,
  is_active: false,
  created_at: "2026-09-13T00:00:00Z",
  updated_at: "2026-09-13T00:00:01Z",
};

describe("model read transport validation", () => {
  it("accepts a valid model profile list", async () => {
    fake.get.mockResolvedValueOnce({ data: [modelProfile] });
    await expect(getModelProfiles()).resolves.toEqual([modelProfile]);
  });

  it("rejects a malformed profile before settings renders it", async () => {
    fake.get.mockResolvedValue({
      data: [{ ...modelProfile, revision: 0 }],
    });
    await expect(getModelProfiles()).rejects.toThrow("model_profiles_invalid");
  });
});

describe("live sync health transport validation", () => {
  const liveSync = {
    source_id: "cursor",
    mode: "watch_and_polling",
    state: "running",
    last_job_id: "job-1",
    last_attempt_at: "2026-09-13T00:00:00Z",
    last_success_at: null,
    last_error_code: null,
  };

  it("accepts valid nullable live-sync fields", async () => {
    fake.get.mockResolvedValue({ data: [liveSync] });
    await expect(getLiveSyncHealth()).resolves.toEqual([liveSync]);
  });

  it.each([
    ["unknown state", { state: "unknown" }],
    ["unknown mode", { mode: "manual" }],
    ["empty job id", { last_job_id: "" }],
    ["control character", { last_error_code: "bad\u0000code" }],
  ])("rejects %s before home renders sync health", async (_name, patch) => {
    fake.get.mockResolvedValue({ data: [{ ...liveSync, ...patch }] });
    await expect(getLiveSyncHealth()).rejects.toThrow(
      "live_sync_health_invalid",
    );
  });

  it("rejects a non-array response", async () => {
    fake.get.mockResolvedValue({ data: { items: [liveSync] } });
    await expect(getLiveSyncHealth()).rejects.toThrow(
      "live_sync_health_invalid",
    );
  });
});

const importJob = {
  job_id: "job-1",
  user_id: "local",
  source_id: "cursor",
  status: "running",
  phase: "read",
  checkpoint: "byte:12",
  discovered: 12,
  discovered_targets: 4,
  emitted: 8,
  stored: 7,
  revised: 1,
  merged: 0,
  skipped: 0,
  errors: 0,
  created_at: "2026-09-13T00:00:00Z",
  updated_at: "2026-09-13T00:00:01Z",
};

describe("import job transport validation", () => {
  it("accepts a complete job and treats an omitted operation as history", async () => {
    fake.get.mockResolvedValue({ data: [importJob] });
    await expect(getImportJobs()).resolves.toEqual([
      { ...importJob, operation: "history" },
    ]);
  });

  it("rejects malformed counters and unknown job fields", async () => {
    fake.get.mockResolvedValue({
      data: [{ ...importJob, stored: -1, arbitrary: "secret" }],
    });
    await expect(getImportJobs()).rejects.toThrow("import_jobs_invalid");
  });

  it("rejects an invalid status or phase before rendering a job", async () => {
    fake.get.mockResolvedValue({
      data: { ...importJob, status: "unknown", phase: "read" },
    });
    await expect(getImportJob("job-1")).rejects.toThrow("import_job_invalid");
    fake.get.mockResolvedValue({
      data: { ...importJob, status: "running", phase: "watch" },
    });
    await expect(getImportJob("job-1")).rejects.toThrow("import_job_invalid");
  });

  it("validates create and change receipts through the same parser", async () => {
    fake.post.mockResolvedValue({
      data: { ...importJob, errors: Number.POSITIVE_INFINITY },
    });
    await expect(createImportJob("cursor")).rejects.toThrow(
      "import_job_invalid",
    );
    await expect(changeImportJob("job-1", "pause")).rejects.toThrow(
      "import_job_invalid",
    );
  });

  it("rejects a malformed issue list", async () => {
    fake.get.mockResolvedValue({
      data: [
        {
          issue_id: 1,
          job_id: "job-1",
          kind: "unknown",
          conversation_id: null,
          reason: "bad",
          created_at: "2026-09-13T00:00:00Z",
        },
      ],
    });
    await expect(getImportIssues("job-1")).rejects.toThrow(
      "import_issues_invalid",
    );
  });
});

describe("source list transport validation", () => {
  const source = {
    source_id: "cursor",
    display_name: "Cursor",
    storage_kinds: ["sqlite"],
    available: true,
    authorized: true,
    detected: true,
    root_label: "Cursor",
    candidate_path: "/Users/example/.cursor",
    candidate_kind: "default",
    imported_messages: 5,
    imported_conversations: 2,
    capabilities: {
      mcp: true,
      hook: true,
      plugin: false,
    },
    sync_completeness: "full_dynamic",
  };

  it("accepts a complete source response", async () => {
    fake.get.mockResolvedValue({ data: [source] });
    await expect(getSources()).resolves.toEqual([source]);
  });

  // Every kind Core can grant, not just the ones this guard happened to know.
  // A source whose root the person picked themselves is `user_selected`; the
  // hand-written list here skipped it, so one such row rejected the whole
  // list and every app disappeared while all endpoints answered 200.
  it.each(["default", "environment_override", "user_selected"])(
    "accepts the %s candidate kind",
    async (candidate_kind) => {
      fake.get.mockResolvedValue({ data: [{ ...source, candidate_kind }] });
      await expect(getSources()).resolves.toEqual([
        { ...source, candidate_kind },
      ]);
    },
  );

  it("rejects a candidate kind Core cannot grant", async () => {
    fake.get.mockResolvedValue({
      data: [{ ...source, candidate_kind: "invented" }],
    });
    await expect(getSources()).rejects.toThrow("invalid source list");
  });

  it.each([
    ["negative messages", { imported_messages: -1 }],
    ["NaN messages", { imported_messages: Number.NaN }],
    [
      "infinite conversations",
      { imported_conversations: Number.POSITIVE_INFINITY },
    ],
    [
      "unsafe conversations",
      { imported_conversations: Number.MAX_SAFE_INTEGER + 1 },
    ],
  ])("rejects %s before rendering the source list", async (_name, patch) => {
    fake.get.mockResolvedValue({ data: [{ ...source, ...patch }] });
    await expect(getSources()).rejects.toThrow("invalid source list");
  });
});

describe("source import receipt transport validation", () => {
  it("accepts the complete measured receipt", async () => {
    fake.get.mockResolvedValue({ data: receipt });
    await expect(getSourceImportReceipt("cursor")).resolves.toEqual(receipt);
  });

  it("rejects a malformed receipt before it can crash the receipt view", async () => {
    const { unknown_time_messages: _missing, ...malformed } = receipt;
    fake.get.mockResolvedValue({ data: malformed });
    await expect(getSourceImportReceipt("cursor")).rejects.toThrow(
      "source_import_receipt_invalid",
    );
  });
});
describe("memory-note list transport validation", () => {
  it("accepts a list of valid note receipts", async () => {
    const note = {
      evidence_id: "11111111-1111-4111-8111-111111111111",
      text: "user note",
      scope: "all-apps",
      created_at: "2026-09-13T00:00:00Z",
      state: "pending",
      task_id: null,
      memory_ids: [],
      error: null,
    };
    fake.get.mockResolvedValue({ data: [note] });
    await expect(getMemoryNotes()).resolves.toEqual([note]);
  });

  it("rejects a malformed note before the composer can crash on it", async () => {
    fake.get.mockResolvedValue({ data: [null] });
    await expect(getMemoryNotes()).rejects.toThrow(
      "memory_note_receipt_invalid",
    );
  });

  it("rejects a malformed operation receipt before it reaches the composer", async () => {
    fake.get.mockResolvedValue({ data: { state: "pending" } });
    await expect(getMemoryNoteOperation("operation-1")).rejects.toThrow(
      "memory_note_receipt_invalid",
    );
  });
});

describe("history file recovery transport validation", () => {
  it("rejects a malformed preview before recovery consumes it", async () => {
    fake.get.mockResolvedValue({
      data: { upload_id: "11111111-1111-4111-8111-111111111111" },
    });
    await expect(
      getHistoryFile("11111111-1111-4111-8111-111111111111"),
    ).rejects.toThrow("file_import_receipt_invalid");
  });
});

describe("model creation recovery transport validation", () => {
  it("rejects a malformed creation operation before the hook reads it", async () => {
    fake.get.mockResolvedValue({ data: { state: "pending" } });
    await expect(getModelCreationOperation("operation-1")).rejects.toThrow(
      "model_creation_operation_invalid",
    );
  });

  it("accepts the two terminal-shaped creation receipts", async () => {
    fake.get.mockResolvedValue({
      data: {
        operation_id: "operation-1",
        state: "committed",
        profile_id: "profile-1",
        created_revision: 1,
      },
    });
    await expect(getModelCreationOperation("operation-1")).resolves.toEqual({
      operation_id: "operation-1",
      state: "committed",
      profile_id: "profile-1",
      created_revision: 1,
    });
  });

  it("rejects an unknown field on a creation receipt", async () => {
    fake.get.mockResolvedValue({
      data: {
        operation_id: "operation-1",
        state: "pending",
        profile_id: null,
        created_revision: null,
        secret: "must-not-render",
      },
    });
    await expect(getModelCreationOperation("operation-1")).rejects.toThrow(
      "model_creation_operation_invalid",
    );
  });
});
