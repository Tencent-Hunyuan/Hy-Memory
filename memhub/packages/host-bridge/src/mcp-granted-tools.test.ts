import { describe, expect, it } from "vitest";

import { type GrantedTools, McpSession } from "./mcp-session.js";
import { MCP_TOOL_NAMES, MCP_TOOLSET_VERSION } from "./mcp-toolset.js";

function sessionWith(granted: GrantedTools): McpSession {
  return new McpSession(
    "/fixture/discovery.json",
    undefined,
    undefined,
    undefined,
    undefined,
    granted,
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
  it("lists exactly recall, write and diagnostic trace when all are granted", async () => {
    const names = await listedNames(
      sessionWith(async () => [
        "om_recall",
        "om_ask",
        "om_conversations",
        "om_diagnostic_trace",
      ]),
    );
    expect(names).toEqual([
      "MemHub_recall",
      "MemHub_write",
      "MemHub_diagnostic_trace",
    ]);
  });

  it("leaves out read tools the grant does not allow", async () => {
    const names = await listedNames(sessionWith(async () => ["om_recall"]));
    expect(names).toEqual(["MemHub_recall", "MemHub_write"]);
  });

  it("keeps the write tool even when nothing is granted", async () => {
    const names = await listedNames(sessionWith(async () => []));
    // Grants govern read tools only. Hiding the write door because a read
    // grant is empty would break it for everyone.
    expect(names).toEqual(["MemHub_write"]);
  });

  it("lists everything when the grant cannot be read", async () => {
    const names = await listedNames(
      sessionWith(async () => {
        throw new Error("broker unreachable");
      }),
    );
    expect(names).toEqual([
      "MemHub_recall",
      "MemHub_write",
      "MemHub_diagnostic_trace",
    ]);
  });

  it("lists everything when there is no grant to read", async () => {
    const names = await listedNames(sessionWith(async () => null));
    expect(names).toHaveLength(3);
  });
});

describe("the tool set stamped on installed entries", () => {
  it("names exactly the tools the server lists", async () => {
    // Change a tool without moving MCP_TOOLSET_VERSION and hosts that cache
    // tool lists by config fingerprint keep showing the old set. Update both,
    // and this pinned version, together.
    const names = await listedNames(sessionWith(async () => null));
    expect(names).toEqual([...MCP_TOOL_NAMES]);
    expect(MCP_TOOLSET_VERSION).toBe("3");
  });
});
