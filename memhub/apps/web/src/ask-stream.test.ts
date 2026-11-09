// Regression coverage for the ask-stream SSE reader.

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  askMemoriesStream,
  parseAskStreamFrame,
  splitAskStreamFrames,
} from "./api";

function streamResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return { ok: true, status: 200, body } as unknown as Response;
}

async function collect(chunks: string[]): Promise<unknown[]> {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamResponse(chunks)));
  const events: unknown[] = [];
  for await (const event of askMemoriesStream("question")) events.push(event);
  return events;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("parseAskStreamFrame", () => {
  it("reads a data line even when the frame starts with another SSE field", () => {
    expect(
      parseAskStreamFrame('event: delta\ndata: {"type":"delta","text":"A"}'),
    ).toEqual({ type: "delta", text: "A" });
  });

  it("joins several data lines before parsing", () => {
    expect(
      parseAskStreamFrame('data: {"type":"delta",\ndata: "text":"B"}'),
    ).toEqual({ type: "delta", text: "B" });
  });

  it("accepts CRLF streams and one optional space after data:", () => {
    expect(parseAskStreamFrame('data: {"type":"done"}\r\n')).toEqual({
      type: "done",
    });
    expect(splitAskStreamFrames('data: {}\r\n\r\ndata: {}')).toEqual([
      "data: {}",
      "data: {}",
    ]);
  });

  it("skips malformed, non-object and data-less frames", () => {
    expect(parseAskStreamFrame("data: {broken}")).toBeNull();
    expect(parseAskStreamFrame("data: null")).toBeNull();
    expect(parseAskStreamFrame("data: [1,2]")).toBeNull();
    expect(parseAskStreamFrame("event: ping")).toBeNull();
    expect(parseAskStreamFrame("")).toBeNull();
  });
});

describe("askMemoriesStream", () => {
  it("yields every event, including a final frame without a trailing blank line", async () => {
    const events = await collect([
      'event: delta\ndata: {"type":"delta","text":"A"}\n\n',
      'data: {broken}\n\n',
      'data: {"type":"delta",\ndata: "text":"B"}\n\n',
      'data: {"type":"done"}',
    ]);

    expect(events).toEqual([
      { type: "delta", text: "A" },
      { type: "delta", text: "B" },
      { type: "done" },
    ]);
  });

  it("does not lose a final frame split across chunks", async () => {
    const events = await collect([
      'data: {"type":"delta"}\n\nda',
      'ta: {"type":"done"}\r\n\r\n',
    ]);

    expect(events).toEqual([{ type: "delta" }, { type: "done" }]);
  });

  it("reports a non-ok response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 503, body: null }),
    );

    await expect(async () => {
      for await (const _event of askMemoriesStream("question")) {
        void _event;
      }
    }).rejects.toThrow("ask_stream_failed_503");
  });
});
