import { mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
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

const RESPONSE = {
  schema_version: 2,
  request_id: "context-request-1",
  state: "grounded",
  items: [
    {
      kind: "raw",
      raw_id: "raw-1",
      content: "Synthetic raw citation.",
      source: "codex",
      session_id: "session-1",
      memory_at: "2026-09-19T00:00:00Z",
      score: 0.8,
      truncated: false,
    },
    {
      kind: "memory",
      memory_id: "memory-1",
      content: "Synthetic SDK memory.",
      layer: "l2_fact",
      status: "active",
      source: "codex",
      updated_at: "2026-09-19T00:00:01Z",
      score: 0.7,
      truncated: false,
    },
  ],
  used_characters: 44,
  truncated: false,
  total_ms: 5,
};

beforeEach(() => {
  vi.clearAllMocks();
  runtime.inspectRuntimeStatus.mockResolvedValue({
    state: "running",
    code: "runtime_verified",
    instanceId: "instance-1",
    host: "127.0.0.1",
    port: 8765,
    version: "1.2.3",
  });
  runtime.discoverRuntimeAttachment.mockResolvedValue(ATTACHED);
  transport.post.mockResolvedValue({ status: 200, data: RESPONSE });
});

describe("unified context search", () => {
  it("accepts an empty raw item in a valid partial context response", async () => {
    transport.post.mockResolvedValue({
      status: 200,
      data: {
        ...RESPONSE,
        state: "partial",
        items: [
          {
            ...RESPONSE.items[0],
            source: "workbuddy",
            content: "",
            truncated: true,
          },
        ],
        used_characters: 0,
        truncated: true,
      },
    });
    const streams = captureStreams();
    try {
      expect(await run(["recall", "test", "--json"])).toBe(0);
    } finally {
      streams.restore();
    }
    expect(JSON.parse(streams.stdout())).toMatchObject({
      schema_version: 2,
      state: "partial",
      items: [{ kind: "raw", content: "" }],
    });
  });

  it("prints useful content without internal ids or protocol labels", async () => {
    const streams = captureStreams();
    try {
      expect(await run(["recall", "synthetic question"])).toBe(0);
    } finally {
      streams.restore();
    }
    const output = streams.stdout();
    expect(output).toContain("Synthetic SDK memory.");
    expect(output).toContain("Synthetic raw citation.");
    expect(output).not.toMatch(/^- raw |memory-1|raw-1|采用状态/m);
  });

  it("labels raw hits as original-conversation excerpts, not memories", async () => {
    const streams = captureStreams();
    try {
      expect(await run(["recall", "synthetic question"])).toBe(0);
    } finally {
      streams.restore();
    }
    const output = streams.stdout();
    expect(output).toContain(
      "- [原始对话摘录 · codex] Synthetic raw citation.",
    );
    expect(output).toContain("- [记忆] Synthetic SDK memory.");
  });

  it("queries the SDK context endpoint directly and preserves item ids", async () => {
    const streams = captureStreams();
    try {
      expect(
        await run([
          "recall",
          "synthetic question",
          "--kind",
          "raw",
          "--kind",
          "memory",
          "--source",
          "codex",
          "--limit",
          "8",
          "--character-budget",
          "4000",
          "--timeout-ms",
          "2000",
          "--json",
        ]),
      ).toBe(0);
    } finally {
      streams.restore();
    }
    expect(transport.post).toHaveBeenCalledWith(
      "/api/v2/context/search",
      {
        query: "synthetic question",
        kinds: ["raw", "memory"],
        raw_source_ids: ["codex"],
        limit: 8,
        character_budget: 4000,
        timeout_ms: 2000,
      },
      undefined,
    );
    const output = JSON.parse(streams.stdout());
    expect(output).toMatchObject({
      schema_version: 2,
      state: "grounded",
      request_id: "context-request-1",
    });
    expect(output).not.toHaveProperty("paths");
    expect(output.items[0]).toMatchObject({ kind: "raw", raw_id: "raw-1" });
    expect(output.items[1]).toMatchObject({
      kind: "memory",
      memory_id: "memory-1",
      layer: "l2_fact",
    });
    // No authority pre-query: released Core always serves the SDK data plane.
    expect(transport.get).not.toHaveBeenCalled();
    expect(transport.put).not.toHaveBeenCalled();
    expect(transport.patch).not.toHaveBeenCalled();
    expect(transport.delete).not.toHaveBeenCalled();
  });

  it("reads stdin only when explicitly requested", async () => {
    const readStdin = vi
      .fn()
      .mockResolvedValue(Buffer.from("sensitive piped query\n"));
    transport.post.mockResolvedValue({
      status: 200,
      data: {
        ...RESPONSE,
        items: [RESPONSE.items[1]],
        used_characters: 21,
      },
    });
    const streams = captureStreams();
    try {
      expect(
        await run(
          ["recall", "--stdin", "--kind", "memory", "--json"],
          {},
          {
            readStdin,
            stdinIsTTY: false,
          },
        ),
      ).toBe(0);
    } finally {
      streams.restore();
    }
    expect(readStdin).toHaveBeenCalledOnce();
    expect(transport.post.mock.calls[0][1]).toMatchObject({
      query: "sensitive piped query",
      kinds: ["memory"],
    });
    expect(streams.stdout()).not.toContain("sensitive piped query");
  });

  it("reads a bounded regular query file without putting its text in output", async () => {
    const directory = await mkdtemp(
      path.join(os.tmpdir(), "om-context-query-"),
    );
    const queryFile = path.join(directory, "query.txt");
    await writeFile(queryFile, "private file query\n");
    const streams = captureStreams();
    try {
      expect(await run(["recall", "--query-file", queryFile, "--json"])).toBe(
        0,
      );
    } finally {
      streams.restore();
      await rm(directory, { recursive: true, force: true });
    }
    expect(transport.post.mock.calls[0][1]).toMatchObject({
      query: "private file query",
    });
    expect(streams.stdout()).not.toContain("private file query");
  });

  it("does not touch stdin when query input is omitted", async () => {
    const readStdin = vi.fn().mockRejectedValue(new Error("must not read"));
    const streams = captureStreams();
    try {
      expect(await run(["recall", "--json"], {}, { readStdin })).toBe(2);
    } finally {
      streams.restore();
    }
    expect(readStdin).not.toHaveBeenCalled();
    expect(transport.post).not.toHaveBeenCalled();
  });

  it("rejects interactive stdin, duplicate inputs, and invalid filters before POST", async () => {
    for (const argv of [
      ["recall", "--stdin", "--json"],
      ["recall", "query", "--stdin", "--json"],
      ["recall", "query", "--kind", "unknown", "--json"],
      ["recall", "query", "--kind", "memory", "--source", "codex", "--json"],
      ["recall", "query", "--limit", "21", "--json"],
      ["recall", "query", "--character-budget", "199", "--json"],
      ["recall", "query", "--timeout-ms", "49", "--json"],
    ]) {
      const streams = captureStreams();
      try {
        expect(
          await run(argv, {}, { stdinIsTTY: argv.includes("--stdin") }),
        ).toBe(2);
      } finally {
        streams.restore();
      }
    }
    expect(transport.post).not.toHaveBeenCalled();
  });

  // The numeric knobs were the one remaining place where a second value was
  // silently used. Every other recall option rejects a duplicate, so a script
  // could not tell which budget/limit actually reached Core.
  it.each([
    ["--limit", "1", "2"],
    ["--character-budget", "200", "300"],
    ["--timeout-ms", "100", "200"],
  ])(
    "rejects a repeated %s before Runtime attach",
    async (flag, first, second) => {
      const streams = captureStreams();
      try {
        expect(
          await run(["recall", "q", flag, first, flag, second, "--json"], {}),
        ).toBe(2);
      } finally {
        streams.restore();
      }
      expect(streams.stdout()).toBe("");
    },
  );

  it("refuses malformed success bodies instead of leaking nested values", async () => {
    transport.post.mockResolvedValue({
      status: 200,
      data: {
        ...RESPONSE,
        items: [
          {
            ...RESPONSE.items[1],
            content: { nested_secret: "must-not-print" },
          },
        ],
        used_characters: 15,
      },
    });
    const streams = captureStreams();
    try {
      expect(await run(["recall", "query", "--json"])).toBe(1);
    } finally {
      streams.restore();
    }
    expect(JSON.parse(streams.stdout())).toMatchObject({
      state: "invalid",
      code: "response_invalid",
    });
    expect(streams.stdout()).not.toContain("must-not-print");
  });

  it("counts characters as code points so an emoji hit stays grounded", async () => {
    // Core reports used_characters with Python's len(), which counts code
    // points: this text is 10 characters there and 12 UTF-16 units here.
    const text = "消费保 📍 扩展 📡";
    expect(text.length).toBe(12);
    expect(Array.from(text).length).toBe(10);
    transport.post.mockResolvedValue({
      status: 200,
      data: {
        ...RESPONSE,
        items: [{ ...RESPONSE.items[0], content: text }],
        used_characters: 10,
      },
    });
    const streams = captureStreams();
    try {
      expect(await run(["recall", "test", "--json"])).toBe(0);
    } finally {
      streams.restore();
    }
    expect(JSON.parse(streams.stdout())).toMatchObject({
      state: "grounded",
      used_characters: 10,
      items: [{ kind: "raw", content: text }],
    });
  });

  it("still rejects a response whose character total does not match", async () => {
    // The same text counted as UTF-16 units is exactly what Core does not mean,
    // so the cross-check must keep failing instead of accepting both totals.
    const text = "消费保 📍 扩展 📡";
    transport.post.mockResolvedValue({
      status: 200,
      data: {
        ...RESPONSE,
        items: [{ ...RESPONSE.items[0], content: text }],
        used_characters: 12,
      },
    });
    const streams = captureStreams();
    try {
      expect(await run(["recall", "test", "--json"])).toBe(1);
    } finally {
      streams.restore();
    }
    expect(JSON.parse(streams.stdout())).toMatchObject({
      state: "invalid",
      code: "response_invalid",
    });
  });

  it.each([
    [
      "a raw item from a source that was not asked for",
      ["--source", "codex"],
      { ...RESPONSE.items[0], source: "cursor" },
    ],
    [
      "a raw item when only memory was asked for",
      ["--kind", "memory"],
      RESPONSE.items[0],
    ],
    ["a score outside 0..1", [], { ...RESPONSE.items[1], score: 1.5 }],
  ])("rejects %s as an invalid response", async (_label, flags, item) => {
    transport.post.mockResolvedValue({
      status: 200,
      data: {
        ...RESPONSE,
        items: [item],
        used_characters: Array.from(String(item.content)).length,
      },
    });
    const streams = captureStreams();
    try {
      expect(await run(["recall", "query", ...flags, "--json"])).toBe(1);
    } finally {
      streams.restore();
    }
    expect(JSON.parse(streams.stdout())).toMatchObject({
      schema_version: 2,
      state: "invalid",
      code: "response_invalid",
    });
  });

  it("reports endpoint denial without exposing the response body", async () => {
    transport.post.mockResolvedValue({
      status: 403,
      data: { detail: "SYNTHETIC_PRIVATE_DETAIL" },
    });
    const streams = captureStreams();
    try {
      expect(await run(["recall", "query", "--json"])).toBe(1);
    } finally {
      streams.restore();
    }
    expect(JSON.parse(streams.stdout())).toMatchObject({
      state: "denied",
      code: "http_403",
    });
    expect(streams.stdout()).not.toContain("SYNTHETIC_PRIVATE_DETAIL");
  });
});

function captureStreams() {
  let stdout = "";
  let stderr = "";
  const out = vi.spyOn(process.stdout, "write").mockImplementation((chunk) => {
    stdout += String(chunk);
    return true;
  });
  const error = vi
    .spyOn(process.stderr, "write")
    .mockImplementation((chunk) => {
      stderr += String(chunk);
      return true;
    });
  return {
    stdout: () => stdout,
    stderr: () => stderr,
    restore: () => {
      out.mockRestore();
      error.mockRestore();
    },
  };
}
