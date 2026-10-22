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

const EVENT = "11111111-1111-4111-8111-111111111111";
const UPLOAD = "22222222-2222-4222-8222-222222222222";
const NOTE = "33333333-3333-4333-8333-333333333333";

beforeEach(() => {
  vi.clearAllMocks();
  // clearAllMocks keeps prior mock implementations, so a mockResolvedValue
  // from an earlier test would leak into the next one and fake its response.
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

describe("calls clear", () => {
  it("reports runtime_stopped (exit 3) without attempting a write", async () => {
    runtime.inspectRuntimeStatus.mockResolvedValue({
      state: "stopped",
      code: "runtime_not_running",
    });
    const result = await capturedRun([
      "calls",
      "clear",
      "--all",
      "--confirm",
      "--json",
    ]);
    expect(result.code).toBe(3);
    expect(JSON.parse(result.stdout).code).toBe("runtime_not_running");
    expect(transport.post).not.toHaveBeenCalled();
  });

  it("previews without contacting Core", async () => {
    const result = await capturedRun([
      "calls",
      "clear",
      "--event",
      EVENT,
      "--json",
    ]);
    expect(result.code).toBe(0);
    const document = JSON.parse(result.stdout);
    expect(document.state).toBe("preview");
    expect(document.plan.kind).toBe("call_events_clear");
    expect(transport.post).not.toHaveBeenCalled();
    expect(transport.delete).not.toHaveBeenCalled();
  });

  it("sends the exact Core body with --confirm", async () => {
    transport.post.mockResolvedValue({ status: 200, data: { removed: 1 } });
    const result = await capturedRun([
      "calls",
      "clear",
      "--event",
      EVENT,
      "--confirm",
      "--json",
    ]);
    expect(result.code).toBe(0);
    expect(transport.post).toHaveBeenCalledWith(
      "/api/v1/call-events/clear",
      { event_ids: [EVENT], user_confirmed: true },
      undefined,
    );
    expect(JSON.parse(result.stdout).state).toBe("committed");
  });

  it("clears all without naming events", async () => {
    transport.post.mockResolvedValue({ status: 200, data: { removed: 4 } });
    const result = await capturedRun([
      "calls",
      "clear",
      "--all",
      "--confirm",
      "--json",
    ]);
    expect(result.code).toBe(0);
    expect(transport.post).toHaveBeenCalledWith(
      "/api/v1/call-events/clear-all",
      { user_confirmed: true },
      undefined,
    );
  });

  it("rejects a malformed event id without contacting Core", async () => {
    const result = await capturedRun([
      "calls",
      "clear",
      "--event",
      "not-a-uuid",
      "--json",
    ]);
    expect(result.code).toBe(2);
    expect(transport.post).not.toHaveBeenCalled();
  });

  it("never reports committed for a malformed success body", async () => {
    transport.post.mockResolvedValue({ status: 200, data: { wrong: true } });
    const result = await capturedRun([
      "calls",
      "clear",
      "--event",
      EVENT,
      "--confirm",
      "--json",
    ]);
    expect(result.code).toBe(1);
    const document = JSON.parse(result.stdout);
    expect(document.state).toBe("unknown");
    expect(document.code).toBe("ui_write_receipt_invalid");
  });
});

describe.skip("retired import-file", () => {
  it("never uploads without --confirm", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "om-import-"));
    const file = path.join(directory, "export.json");
    await writeFile(file, '{"conversations":[]}', { mode: 0o600 });
    const result = await capturedRun([
      "import-file",
      "upload",
      "--file",
      file,
      "--json",
    ]);
    expect(result.code).toBe(0);
    const document = JSON.parse(result.stdout);
    expect(document.state).toBe("preview");
    expect(document.plan.operation_id).toBeDefined();
    expect(transport.post).not.toHaveBeenCalled();
  });

  it("uploads the file with a deterministic operation id on confirm", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "om-import-"));
    const file = path.join(directory, "export.json");
    await writeFile(file, '{"conversations":[]}', { mode: 0o600 });
    transport.post.mockResolvedValue({
      status: 201,
      data: {
        schema_version: 1,
        operation_id: "ignored-here",
        state: "completed",
        upload_id: UPLOAD,
        upload_state: "staged",
      },
    });
    const preview = await capturedRun([
      "import-file",
      "upload",
      "--file",
      file,
      "--json",
    ]);
    const plan = JSON.parse(preview.stdout).plan;
    transport.post.mockResolvedValue({
      status: 201,
      data: {
        schema_version: 1,
        operation_id: plan.operation_id,
        state: "completed",
        upload_id: UPLOAD,
        upload_state: "staged",
      },
    });
    const result = await capturedRun([
      "import-file",
      "upload",
      "--file",
      file,
      "--confirm",
      "--json",
    ]);
    expect(result.code).toBe(0);
    const url = transport.post.mock.calls.at(-1)?.[0] as string;
    expect(url).toContain("/api/v1/import-files?");
    expect(url).toContain(`operation_id=${plan.operation_id}`);
    expect(url).toContain("filename=export.json");
    expect(JSON.parse(result.stdout).state).toBe("committed");
  });

  it("commits and discards only with explicit confirmation", async () => {
    const preview = await capturedRun([
      "import-file",
      "commit",
      UPLOAD,
      "--json",
    ]);
    expect(preview.code).toBe(0);
    expect(JSON.parse(preview.stdout).state).toBe("preview");
    expect(transport.post).not.toHaveBeenCalled();

    transport.post.mockResolvedValue({
      status: 200,
      data: {
        upload_id: UPLOAD,
        filename: "x.json",
        vendor: "generic",
        source_id: "generic-export",
        messages: 0,
        conversations: 0,
        human_messages: 0,
        agent_messages: 0,
        skipped_records: 0,
        ignored_files: 0,
        checksum: "a".repeat(64),
        created_at: "T",
        committed: true,
      },
    });
    const committed = await capturedRun([
      "import-file",
      "commit",
      UPLOAD,
      "--confirm",
      "--json",
    ]);
    expect(committed.code).toBe(0);
    expect(transport.post).toHaveBeenCalledWith(
      `/api/v1/import-files/${UPLOAD}/commit`,
      { confirmed: true },
      undefined,
    );

    transport.delete.mockResolvedValue({ status: 204, data: "" });
    const discarded = await capturedRun([
      "import-file",
      "discard",
      UPLOAD,
      "--confirm",
      "--json",
    ]);
    expect(discarded.code).toBe(0);
    expect(JSON.parse(discarded.stdout).state).toBe("committed");
    expect(transport.delete).toHaveBeenCalledWith(
      `/api/v1/import-files/${UPLOAD}`,
      undefined,
    );
  });

  it("maps a Core 404 to a stable rejected code, not a fake success", async () => {
    transport.post.mockResolvedValue({
      status: 404,
      data: { detail: "import_file_not_found" },
    });
    const result = await capturedRun([
      "import-file",
      "commit",
      UPLOAD,
      "--confirm",
      "--json",
    ]);
    expect(result.code).toBe(1);
    const document = JSON.parse(result.stdout);
    expect(document.state).toBe("rejected");
    expect(document.code).toBe("import_file_not_found");
  });
});

describe("notes / extraction / graph / manual extract", () => {
  it("adds a note from a bounded file and re-verifies content on apply", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "om-note-"));
    const content = path.join(directory, "note.txt");
    await writeFile(content, "记住这个决定", { mode: 0o600 });
    const preview = await capturedRun([
      "notes",
      "add",
      "--content-file",
      content,
      "--json",
    ]);
    expect(preview.code).toBe(0);
    const planFile = path.join(directory, "plan.json");
    await writeFile(planFile, preview.stdout, { mode: 0o600 });
    transport.post.mockResolvedValue({
      status: 201,
      // A realistic Core `MemoryNoteReceipt`: the shared parser requires
      // `scope` and `memory_ids` too, so a minimal `{evidence_id,state}` stub
      // is a malformed body, not a success.
      data: {
        evidence_id: NOTE,
        text: "记住这个决定",
        scope: "all-apps",
        created_at: "2026-09-19T00:00:00Z",
        state: "pending",
        task_id: null,
        memory_ids: [],
        error: null,
      },
    });
    const applied = await capturedRun([
      "notes",
      "apply",
      "--plan-file",
      planFile,
      "--content-file",
      content,
      "--confirm",
      "--json",
    ]);
    expect(applied.code).toBe(0);
    const body = transport.post.mock.calls.at(-1)?.[1] as Record<
      string,
      unknown
    >;
    expect(body.text).toBe("记住这个决定");
    expect(body.scope).toBe("all-apps");
    expect(JSON.parse(applied.stdout).state).toBe("committed");
  });

  it.skip("plans then applies the extraction window", async () => {
    const preview = await capturedRun([
      "extraction",
      "set",
      "--window",
      "6m",
      "--json",
    ]);
    expect(preview.code).toBe(0);
    expect(JSON.parse(preview.stdout).state).toBe("preview");
    expect(transport.put).not.toHaveBeenCalled();
    transport.put.mockResolvedValue({
      status: 200,
      data: { window_preset: "6m" },
    });
    const applied = await capturedRun([
      "extraction",
      "set",
      "--window",
      "6m",
      "--confirm",
      "--json",
    ]);
    expect(applied.code).toBe(0);
    expect(transport.put).toHaveBeenCalledWith(
      "/api/v1/extraction-settings",
      { window_preset: "6m" },
      undefined,
    );
  });

  it("does not call graph width without --confirm", async () => {
    const preview = await capturedRun([
      "graph",
      "width",
      "--decision",
      "rebuild_with_new",
      "--json",
    ]);
    expect(preview.code).toBe(0);
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
      "--confirm",
      "--json",
    ]);
    expect(applied.code).toBe(0);
    expect(transport.post).toHaveBeenCalledWith(
      "/api/v1/graph-width",
      { decision: "rebuild_with_new" },
      undefined,
    );
  });

  it.skip("plans manual extraction and applies it only on confirm", async () => {
    const preview = await capturedRun([
      "processing",
      "extract",
      "--evidence",
      NOTE,
      "--json",
    ]);
    expect(preview.code).toBe(0);
    expect(JSON.parse(preview.stdout).state).toBe("preview");
    expect(transport.post).not.toHaveBeenCalled();
    transport.post.mockResolvedValue({
      status: 200,
      data: { promoted: 1, batches_planned: 2, batches_current: 1 },
    });
    const applied = await capturedRun([
      "processing",
      "extract",
      "--evidence",
      NOTE,
      "--confirm",
      "--json",
    ]);
    expect(applied.code).toBe(0);
    expect(transport.post).toHaveBeenCalledWith(
      "/api/v1/memory-processing/manual-extract",
      { evidence_ids: [NOTE] },
      undefined,
    );
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
