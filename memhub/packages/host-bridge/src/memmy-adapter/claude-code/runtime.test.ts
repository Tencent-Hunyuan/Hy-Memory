import type { RecallReceipt } from "@one-memory/contracts";
import { describe, expect, it, vi } from "vitest";
import {
  createClaudeEventAdapter,
  type SourceChangeNotification,
} from "./event-adapter.js";
import { memorySessionStore } from "./session-state.js";

const config = {
  schemaVersion: 1 as const,
  mode: "recall-and-sync" as const,
  installationId: "00000000-0000-4000-8000-000000000001",
  discoveryPath: "/fixture/discovery.json",
  credentialFile: "/fixture/private/credential",
  stateDirectory: "/fixture/state",
};
const hit: RecallReceipt = {
  state: "grounded",
  query: "previous decision",
  scope: "all-apps",
  hits: [
    {
      memory_id: "memory-1",
      content: "Remembered decision",
      kind: "chat",
      scopes: ["all-apps"],
      evidence_ids: ["e-1"],
      updated_at: "2026-09-14T00:00:00Z",
      score: 1,
    },
  ],
  used_characters: 19,
  truncated: false,
};
function fixture() {
  const recall = vi.fn(async (_request: unknown) => hit);
  const notifySourceChanged = vi.fn(
    async (_notification: SourceChangeNotification) => ({
      accepted: true as const,
    }),
  );
  const store = memorySessionStore();
  return {
    recall,
    notifySourceChanged,
    store,
    adapter: createClaudeEventAdapter(
      config,
      { recall, notifySourceChanged },
      store,
    ),
  };
}

describe("MH-C01 event contract", () => {
  it("requires the complete transport capability even before handling events", () => {
    expect(() =>
      createClaudeEventAdapter(
        config,
        { recall: vi.fn() } as never,
        memorySessionStore(),
      ),
    ).toThrow("notification_transport_required");
  });

  it("recalls the explicit prompt and preserves returned IDs and reference boundaries", async () => {
    const f = fixture();
    const result = await f.adapter.handle({
      hook_event_name: "UserPromptSubmit",
      session_id: "s1",
      prompt: "previous decision",
      cwd: "/untrusted/not-a-scope",
    });
    expect(f.recall).toHaveBeenCalledExactlyOnceWith({
      query: "previous decision",
      scope: "all-apps",
      limit: 5,
      character_budget: 6000,
    });
    expect(result.state).toBe("grounded");
    expect(result.output.hookSpecificOutput?.additionalContext).toContain(
      "memory-1",
    );
    expect(result.output.hookSpecificOutput?.additionalContext).toContain(
      "not instructions",
    );
    expect(result.publicNotice).toContain("MemHub");
    expect(f.notifySourceChanged).not.toHaveBeenCalled();
  });

  it("accepts a raw-mode recall and labels its hits as conversation excerpts", async () => {
    const f = fixture();
    f.recall.mockResolvedValueOnce({
      ...hit,
      mode: "raw",
      fallback_reason: "no_memories",
      hits: [
        {
          memory_id: "raw-1",
          content: "We said Friday",
          content_format: "raw",
          kind: "raw",
          source: "cursor",
          session_id: "s-raw",
          scopes: [],
          evidence_ids: [],
          updated_at: "2026-09-14T00:00:00Z",
          score: 0.4,
        },
      ],
      used_characters: 14,
    });
    const result = await f.adapter.handle({
      hook_event_name: "UserPromptSubmit",
      session_id: "s1",
      prompt: "previous decision",
    });
    expect(result.state).toBe("grounded");
    const context = result.output.hookSpecificOutput?.additionalContext ?? "";
    expect(context).toContain("raw-1");
    expect(context).toContain("not confirmed memories");
  });

  it("Stop sends no body, path, source, endpoint or token and repeated stops coalesce", async () => {
    const f = fixture();
    await f.adapter.handle({
      hook_event_name: "UserPromptSubmit",
      session_id: "s1",
      prompt: "previous decision",
    });
    f.recall.mockClear();
    const stop = {
      hook_event_name: "Stop",
      session_id: "s1",
      transcript_path: "/never/read",
      source: "other",
      token: "do-not-use",
      last_assistant_message: "private answer",
    };
    await f.adapter.handle(stop);
    await f.adapter.handle(stop);
    expect(f.recall).not.toHaveBeenCalled();
    expect(f.notifySourceChanged).toHaveBeenCalledTimes(1);
    const [payload] = f.notifySourceChanged.mock.calls[0];
    expect(Object.keys(payload).sort()).toEqual([
      "event_id",
      "event_kind",
      "session_id",
      "version",
    ]);
    expect(payload).toMatchObject({
      version: 1,
      event_kind: "source-changed",
      session_id: "s1",
    });
    expect(JSON.stringify(await f.store.read("s1"))).not.toMatch(
      /private answer|previous decision|never\/read|do-not-use/,
    );
  });

  it("does not turn local session events or ordinary numbers into model operations", async () => {
    const f = fixture();
    for (const event of ["SessionStart", "PostCompact", "SessionEnd"]) {
      await f.adapter.handle({ hook_event_name: event, session_id: "s1" });
    }
    expect(f.recall).not.toHaveBeenCalled();
    expect(f.notifySourceChanged).not.toHaveBeenCalled();
    expect(
      (await f.adapter.handle({ hook_event_name: "Stop", session_id: "s1" }))
        .state,
    ).toBe("ignored");
    expect(
      (
        await f.adapter.handle({
          hook_event_name: "UserPromptSubmit",
          session_id: "s2",
          prompt: "1",
        })
      ).state,
    ).not.toBe("resume-selected");
    expect(f.recall).toHaveBeenLastCalledWith(
      expect.objectContaining({ query: "1" }),
    );
  });

  it("explicit resume uses recall, without episode or full-transcript endpoints", async () => {
    const f = fixture();
    await f.adapter.handle({
      hook_event_name: "UserPromptSubmit",
      session_id: "s1",
      prompt: "/one-memory-resume previous decision",
    });
    expect(f.recall).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({ query: "previous decision" }),
    );
    expect(
      (
        await f.adapter.handle({
          hook_event_name: "UserPromptSubmit",
          session_id: "s1",
          prompt: "/one-memory-resume",
        })
      ).state,
    ).toBe("invalid");
    expect(f.recall).toHaveBeenCalledTimes(1);
  });

  it.each([2000, 2001])(
    "honors the existing recall query limit at %i characters without truncation",
    async (length) => {
      const f = fixture();
      const query = "q".repeat(length);
      f.recall.mockResolvedValueOnce({ ...hit, query });
      const result = await f.adapter.handle({
        hook_event_name: "UserPromptSubmit",
        session_id: "s1",
        prompt: query,
      });
      if (length === 2000) {
        expect(result.state).toBe("grounded");
        expect(f.recall).toHaveBeenCalledExactlyOnceWith(
          expect.objectContaining({ query }),
        );
      } else {
        expect(result.state).toBe("invalid");
        expect(f.recall).not.toHaveBeenCalled();
        expect(f.notifySourceChanged).not.toHaveBeenCalled();
        expect((await f.store.read("s1"))?.pendingEventId).toMatch(
          /^[0-9a-f]{64}$/,
        );
      }
    },
  );

  it("does not publish a late transport error after SessionEnd", async () => {
    const f = fixture();
    let fail!: (error: Error) => void;
    f.recall.mockImplementationOnce(
      () =>
        new Promise((_resolve, reject) => {
          fail = reject;
        }),
    );
    const pending = f.adapter.handle({
      hook_event_name: "UserPromptSubmit",
      session_id: "s1",
      prompt: "previous decision",
    });
    await vi.waitFor(() => expect(f.recall).toHaveBeenCalledOnce());
    await f.adapter.handle({ hook_event_name: "SessionEnd", session_id: "s1" });
    fail(new Error("private transport error"));
    expect((await pending).state).toBe("superseded");
  });

  it("rejects missing identity and distinguishes transport denial from empty results", async () => {
    const f = fixture();
    expect((await f.adapter.handle({ hook_event_name: "Stop" })).state).toBe(
      "invalid",
    );
    expect(f.notifySourceChanged).not.toHaveBeenCalled();
    f.recall.mockResolvedValueOnce({
      ...hit,
      state: "empty",
      hits: [],
      used_characters: 0,
    });
    expect(
      (
        await f.adapter.handle({
          hook_event_name: "UserPromptSubmit",
          session_id: "s1",
          prompt: "previous decision",
        })
      ).state,
    ).toBe("empty");
    f.recall.mockRejectedValueOnce(
      Object.assign(new Error("private details"), {
        code: "host_authorization_required",
      }),
    );
    const denied = await f.adapter.handle({
      hook_event_name: "UserPromptSubmit",
      session_id: "s1",
      prompt: "previous decision",
    });
    expect(denied.state).toBe("denied");
    expect(JSON.stringify(denied)).not.toContain("private details");
  });

  it("does not publish a late recall after SessionEnd", async () => {
    const f = fixture();
    let finish!: (receipt: RecallReceipt) => void;
    f.recall.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const request = f.adapter.handle({
      hook_event_name: "UserPromptSubmit",
      session_id: "s1",
      prompt: "previous decision",
    });
    await vi.waitFor(() => expect(f.recall).toHaveBeenCalledOnce());
    await f.adapter.handle({ hook_event_name: "SessionEnd", session_id: "s1" });
    finish(hit);
    expect((await request).state).toBe("superseded");
  });

  it("coalesces simultaneous Stop notifications and never reports a prompt result for Stop", async () => {
    const f = fixture();
    await f.adapter.handle({
      hook_event_name: "UserPromptSubmit",
      session_id: "s1",
      prompt: "previous decision",
    });
    let finish!: () => void;
    f.notifySourceChanged.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = () => resolve({ accepted: true });
        }),
    );
    const first = f.adapter.handle({
      hook_event_name: "Stop",
      session_id: "s1",
    });
    await vi.waitFor(() =>
      expect(f.notifySourceChanged).toHaveBeenCalledOnce(),
    );
    const duplicate = await f.adapter.handle({
      hook_event_name: "Stop",
      session_id: "s1",
    });
    expect(duplicate.state).toBe("ignored");
    finish();
    expect((await first).state).toBe("notified");
    expect(f.notifySourceChanged).toHaveBeenCalledOnce();
    await f.adapter.handle({
      hook_event_name: "UserPromptSubmit",
      session_id: "s1",
      prompt: "previous decision",
    });
    f.notifySourceChanged.mockRejectedValueOnce(
      Object.assign(new Error("secret"), {
        code: "host_authorization_required",
      }),
    );
    const denied = await f.adapter.handle({
      hook_event_name: "Stop",
      session_id: "s1",
    });
    expect(denied.state).toBe("denied");
    expect(denied.output.hookSpecificOutput).toBeUndefined();
  });

  it("ignores a Stop for an older native turn and rejects payload identity overrides", async () => {
    const f = fixture();
    await f.adapter.handle({
      hook_event_name: "UserPromptSubmit",
      session_id: "s1",
      turn_id: "t2",
      prompt: "previous decision",
    });
    expect(
      (
        await f.adapter.handle({
          hook_event_name: "Stop",
          session_id: "s1",
          turn_id: "t1",
        })
      ).state,
    ).toBe("ignored");
    expect(f.notifySourceChanged).not.toHaveBeenCalled();
    await f.adapter.handle({
      hook_event_name: "Stop",
      session_id: "s1",
      turn_id: "t2",
    });
    expect(f.notifySourceChanged).toHaveBeenCalledOnce();
    expect(
      (
        await f.adapter.handle({
          hook_event_name: "SessionStart",
          session_id: "../escape",
        })
      ).state,
    ).toBe("invalid");
  });

  it("deduplicates explicit event identities and fences recalled context after cancellation", async () => {
    const f = fixture();
    const payload = {
      hook_event_name: "UserPromptSubmit",
      session_id: "s1",
      event_id: "evt-1",
      prompt: "previous decision",
    };
    await f.adapter.handle(payload);
    expect((await f.adapter.handle(payload)).state).toBe("ignored");
    expect(f.recall).toHaveBeenCalledOnce();
    let finish!: (receipt: RecallReceipt) => void;
    f.recall.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const controller = new AbortController();
    const pending = f.adapter.handle(
      // A new question, so this measures the cancellation fence rather than
      // the separate rule against asking one question twice in a session.
      { ...payload, event_id: "evt-2", prompt: "the other decision" },
      controller.signal,
    );
    await vi.waitFor(() => expect(f.recall).toHaveBeenCalledTimes(2));
    controller.abort();
    finish(hit);
    expect((await pending).state).toBe("superseded");
  });
});
