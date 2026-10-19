import { describe, expect, it, vi } from "vitest";

import { type GrantedTools, McpSession } from "./mcp-session.js";

function sessionWith(
  granted: GrantedTools,
  genericLocalMcp = false,
): McpSession {
  return new McpSession(
    "/fixture/discovery.json",
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    granted,
    genericLocalMcp,
  );
}

async function listedNames(session: McpSession): Promise<string[]> {
  const response = await session.handle({
    jsonrpc: "2.0",
    id: 1,
    method: "tools/list",
  });
  const result = (response as { result: { tools: Array<{ name: string }> } })
    .result;
  return result.tools.map((tool) => tool.name);
}

describe("BUG-15: the listed tools follow what the grant allows", () => {
  it("lists every read tool the grant allows", async () => {
    const names = await listedNames(
      sessionWith(async () => [
        "om_recall",
        "om_ask",
        "om_conversations",
        "om_matters",
        "om_diagnostic_trace",
      ]),
    );
    expect(names).toContain("MemHub_recall");
    expect(names).toContain("MemHub_ask");
    expect(names).toContain("MemHub_conversations");
    expect(names).toContain("MemHub_matters");
    expect(names).toContain("MemHub_diagnostic_trace");
  });

  it("leaves out read tools the grant does not allow", async () => {
    const names = await listedNames(sessionWith(async () => ["om_recall"]));
    expect(names).toContain("MemHub_recall");
    expect(names).not.toContain("MemHub_ask");
    expect(names).not.toContain("MemHub_conversations");
    expect(names).not.toContain("MemHub_matters");
    expect(names).not.toContain("MemHub_diagnostic_trace");
  });

  it("keeps tools a grant does not govern even when nothing is granted", async () => {
    const names = await listedNames(sessionWith(async () => []));
    expect(names).not.toContain("MemHub_recall");
    // Grants govern read tools only. Hiding the write and context doors
    // because a read grant is empty would break them for everyone.
    expect(names).toContain("MemHub_context");
    expect(names).toContain("MemHub_memory_write");
    expect(names).toContain("MemHub_memory_write_status");
  });

  it("lists everything when the grant cannot be read", async () => {
    const names = await listedNames(
      sessionWith(async () => {
        throw new Error("broker unreachable");
      }),
    );
    expect(names).toContain("MemHub_recall");
    expect(names).toContain("MemHub_conversations");
    expect(names).toContain("MemHub_diagnostic_trace");
  });

  it("lists everything when there is no grant to read", async () => {
    const names = await listedNames(sessionWith(async () => null));
    expect(names).toContain("MemHub_recall");
    expect(names).toContain("MemHub_conversations");
  });

  it.each(["failed", "missing", "overbroad"])(
    "lists only the two generic tools when the grant query is %s",
    async (mode) => {
      const granted: GrantedTools = async () => {
        if (mode === "failed") throw new Error("broker unavailable");
        return mode === "missing" ? null : ["om_conversations", "om_ask"];
      };
      expect(await listedNames(sessionWith(granted, true))).toEqual([
        "MemHub_recall",
        "MemHub_context",
      ]);
    },
  );

  it("refuses hidden generic tools on calls without invoking their handlers", async () => {
    const conversations = vi.fn(async () => {
      throw new Error("called");
    });
    const memoryWrite = vi.fn(async () => {
      throw new Error("called");
    });
    const session = new McpSession(
      "/fixture/discovery.json",
      undefined,
      undefined,
      undefined,
      conversations,
      undefined,
      undefined,
      memoryWrite,
      undefined,
      undefined,
      async () => null,
      true,
    );
    for (const [name, args] of [
      ["MemHub_conversations", { query: "original" }],
      ["om_conversations", { query: "original" }],
      [
        "MemHub_memory_write",
        {
          internal_session_id: "fixture",
          first_round: 1,
          last_round: 1,
          evidence_ids: ["e1"],
        },
      ],
      ["MemHub_memory_write_status", { request_id: "fixture" }],
      ["MemHub_ask", { question: "fixture" }],
      ["MemHub_matters", { query: "fixture" }],
      ["MemHub_diagnostic_trace", { request_id: "fixture" }],
    ] as const) {
      const response = await session.handle({
        jsonrpc: "2.0",
        id: 1,
        method: "tools/call",
        params: { name, arguments: args },
      });
      expect(response).toMatchObject({
        error: {
          code: -32602,
          message: "Tool not available for this connection",
        },
      });
    }
    expect(conversations).not.toHaveBeenCalled();
    expect(memoryWrite).not.toHaveBeenCalled();
  });
});
