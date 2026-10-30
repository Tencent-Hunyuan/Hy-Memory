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
  stopRuntimeProcess: vi.fn(),
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

const JOB = {
  job_id: "job-1",
  user_id: "local",
  source_id: "codex",
  operation: "history" as const,
  status: "paused" as const,
  phase: null,
  checkpoint: null,
  discovered: 1,
  discovered_targets: 1,
  emitted: 1,
  stored: 1,
  revised: 0,
  merged: 0,
  skipped: 0,
  errors: 0,
  created_at: "2026-09-30T00:00:00Z",
  updated_at: "2026-09-30T00:01:00Z",
};

beforeEach(() => {
  vi.clearAllMocks();
  runtime.inspectRuntimeStatus.mockResolvedValue(RUNNING);
  runtime.discoverRuntimeAttachment.mockResolvedValue(ATTACHED);
  transport.post.mockResolvedValue({ status: 200, data: JOB });
});

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

describe("memhub jobs", () => {
  it("rejects a repeated --json before any request", async () => {
    const output = captureStdout();
    let code: number;
    try {
      code = await run(["jobs", "list", "--json", "--json"], {});
    } finally {
      output.restore();
    }
    expect(code!).toBe(2);
    expect(transport.post).not.toHaveBeenCalled();
  });

  it("start posts directly, without a preview step", async () => {
    const output = captureStdout();
    let code: number;
    try {
      code = await run(["jobs", "start", "--source", "codex", "--json"], {});
    } finally {
      output.restore();
    }
    expect(code!).toBe(0);
    expect(transport.post).toHaveBeenCalledWith(
      "/api/v1/import-jobs",
      { source_id: "codex", operation: "history" },
      undefined,
    );
  });

  it("start still accepts a legacy --confirm", async () => {
    const output = captureStdout();
    let code: number;
    try {
      code = await run(
        [
          "jobs",
          "start",
          "--source",
          "codex",
          "--operation",
          "sync",
          "--confirm",
          "--json",
        ],
        {},
      );
    } finally {
      output.restore();
    }
    expect(code!).toBe(0);
    expect(transport.post).toHaveBeenCalledWith(
      "/api/v1/import-jobs",
      { source_id: "codex", operation: "sync" },
      undefined,
    );
  });

  it.each(["pause", "resume", "cancel"] as const)(
    "%s posts the action endpoint directly",
    async (action) => {
      const output = captureStdout();
      let code: number;
      try {
        code = await run(["jobs", action, "--job", "job-1", "--json"], {});
      } finally {
        output.restore();
      }
      expect(code!).toBe(0);
      expect(transport.post).toHaveBeenCalledWith(
        `/api/v1/import-jobs/job-1/${action}`,
        {},
        undefined,
      );
    },
  );

  it.each(["pause", "resume", "cancel"] as const)(
    "%s --confirm posts the exact action endpoint",
    async (action) => {
      const output = captureStdout();
      let code: number;
      try {
        code = await run(
          ["jobs", action, "--job", "job-1", "--confirm", "--json"],
          {},
        );
      } finally {
        output.restore();
      }
      expect(code!).toBe(0);
      expect(transport.post).toHaveBeenCalledWith(
        `/api/v1/import-jobs/job-1/${action}`,
        {},
        undefined,
      );
    },
  );
});
