import { describe, expect, it, vi } from "vitest";

import { processMcpLine } from "./mcp-protocol.js";
import { McpSession } from "./mcp-session.js";

describe("MemHub MCP protocol", () => {
  it("negotiates MCP and exposes exactly recall, write and diagnostic trace", async () => {
    const session = new McpSession("/fixture/discovery.json");
    const initialized = JSON.parse(
      (await processMcpLine(
        session,
        JSON.stringify({
          jsonrpc: "2.0",
          id: 1,
          method: "initialize",
          params: { protocolVersion: "2025-03-26" },
        }),
      )) ?? "",
    );
    const listed = JSON.parse(
      (await processMcpLine(
        session,
        JSON.stringify({
          jsonrpc: "2.0",
          id: 2,
          method: "tools/list",
        }),
      )) ?? "",
    );

    expect(initialized.result.protocolVersion).toBe("2025-03-26");
    expect(initialized.result.instructions).toContain("MemHub");
    expect(initialized.result.instructions).toContain("not proof of adoption");
    expect(listed.result.tools[0].description).not.toContain("user-confirmed");
    expect(
      listed.result.tools.map((tool: { name: string }) => tool.name),
    ).toEqual(["MemHub_recall", "MemHub_write", "MemHub_diagnostic_trace"]);
    // The old context guidance is no longer part of the handshake.
    expect(initialized.result.instructions).not.toContain("MemHub_context");
    expect(initialized.result.instructions).not.toContain("MemHub_ask");
    // Hosts decide whether to ask the user for permission from these, not from
    // the description. Without them OpenClaw prompted for approval on every
    // recall, which turns a background read into an interruption.
    expect(listed.result.tools[0].annotations).toMatchObject({
      readOnlyHint: true,
      destructiveHint: false,
      openWorldHint: false,
    });
    const write = listed.result.tools[1];
    expect(write.annotations).toMatchObject({
      readOnlyHint: false,
      destructiveHint: false,
      idempotentHint: true,
    });
    expect(write.inputSchema.required).toEqual(["text"]);
    expect(Object.keys(write.inputSchema.properties).sort()).toEqual([
      "request_id",
      "text",
    ]);
  });

  it.each([
    "MemHub_ask",
    "MemHub_conversations",
    "MemHub_context",
    "MemHub_memory_write",
    "MemHub_memory_write_status",
    "om_ask",
    "om_context",
    "om_memory_write",
  ])("rejects the removed tool %s as unknown", async (name) => {
    const session = new McpSession("/fixture/discovery.json");
    const response = await session.handle({
      jsonrpc: "2.0",
      id: 3,
      method: "tools/call",
      params: { name, arguments: { query: "x", question: "x" } },
    });
    expect(response?.error).toEqual({
      code: -32602,
      message: `Unknown tool: ${name}`,
    });
  });

  it("forwards one diagnostic request id through the read-only trace tool", async () => {
    const trace = vi.fn(async () => ({
      schema_version: 1 as const,
      state: "ok" as const,
      events: [{ event: "memory_task.finished", duration_ms: 17 }],
    }));
    const session = new McpSession(
      "/fixture/discovery.json",
      undefined,
      undefined,
      undefined,
      trace,
    );
    const response = JSON.parse(
      (await processMcpLine(
        session,
        JSON.stringify({
          jsonrpc: "2.0",
          id: 9,
          method: "tools/call",
          params: {
            name: "MemHub_diagnostic_trace",
            arguments: { request_id: "request-1" },
          },
        }),
      )) ?? "",
    );

    expect(trace).toHaveBeenCalledWith("/fixture/discovery.json", {
      request_id: "request-1",
    });
    expect(response.result.structuredContent.state).toBe("ok");
  });

  it("passes bounded arguments to the broker and returns structured content", async () => {
    const recall = vi.fn(async () => ({
      state: "grounded" as const,
      query: "我的偏好",
      scope: "all-apps",
      hits: [],
      used_characters: 0,
      truncated: false,
    }));
    // No workspace, so this test stays about the argument bounds; the
    // directory a host declares is covered on its own below.
    const session = new McpSession(
      "/fixture/discovery.json",
      recall,
      undefined,
    );
    const response = JSON.parse(
      (await processMcpLine(
        session,
        JSON.stringify({
          jsonrpc: "2.0",
          id: "recall-1",
          method: "tools/call",
          params: {
            name: "om_recall",
            arguments: {
              query: "我的偏好",
              limit: 3,
              character_budget: 800,
            },
          },
        }),
      )) ?? "",
    );

    expect(recall).toHaveBeenCalledWith("/fixture/discovery.json", {
      query: "我的偏好",
      limit: 3,
      character_budget: 800,
    });
    expect(response.result.structuredContent.state).toBe("grounded");
  });

  it("returns an explicit tool error when MemHub is closed", async () => {
    const session = new McpSession("/fixture/discovery.json", async () => {
      throw new Error("MemHub is not running");
    });
    const response = JSON.parse(
      (await processMcpLine(
        session,
        JSON.stringify({
          jsonrpc: "2.0",
          id: 7,
          method: "tools/call",
          params: { name: "om_recall", arguments: { query: "anything" } },
        }),
      )) ?? "",
    );

    expect(response.result.isError).toBe(true);
    expect(response.result.content[0].text).toContain("MemHub is not running");
  });

  it("rejects malformed JSON and out-of-range tool input", async () => {
    const session = new McpSession("/fixture/discovery.json");
    const parseError = JSON.parse((await processMcpLine(session, "{")) ?? "");
    const invalidInput = JSON.parse(
      (await processMcpLine(
        session,
        JSON.stringify({
          jsonrpc: "2.0",
          id: 8,
          method: "tools/call",
          params: {
            name: "om_recall",
            arguments: { query: "x", limit: 200 },
          },
        }),
      )) ?? "",
    );

    expect(parseError.error.code).toBe(-32700);
    expect(invalidInput.error.code).toBe(-32602);
  });

  it("attaches the project the host says it is in", async () => {
    const recall = vi.fn(async () => ({
      state: "empty" as const,
      query: "结论",
      scope: "all-apps",
      hits: [],
      used_characters: 0,
      truncated: false,
    }));
    const session = new McpSession(
      "/fixture/discovery.json",
      recall,
      "/Users/example/hy-memory-admin",
    );

    await session.handle({
      jsonrpc: "2.0",
      id: 1,
      method: "tools/call",
      params: { name: "om_recall", arguments: { query: "结论" } },
    });

    // The model is never asked for this. It could name a directory it is not
    // in, and that is the one boundary the workspace design draws.
    expect(recall).toHaveBeenCalledWith("/fixture/discovery.json", {
      query: "结论",
      workspace_path: "/Users/example/hy-memory-admin",
    });
  });
});
