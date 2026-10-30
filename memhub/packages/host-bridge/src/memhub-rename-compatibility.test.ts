import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { DSH_ROW_ID, DSH_SERVER_NAME } from "./dsh-patch.js";
import { processMcpLine } from "./mcp-protocol.js";
import { McpSession } from "./mcp-session.js";
import {
  OWNER_VALUE,
  type RecallRuntime,
  renderConnectionMaterial,
  renderSkillContent,
  SERVER_NAME,
} from "./recall-installer.js";

const QUERY_NOTICE = "正在使用 MemHub 查询记忆";
const runtime: RecallRuntime = {
  runtimeCommand: "/fixture/runtime",
  mcpEntryPath: "/fixture/mcp.cjs",
  discoveryPath: "/fixture/discovery.json",
  recallCliPath: "/fixture/recall-cli.cjs",
};

describe("MemHub rename keeps technical identities", () => {
  it("does not move the MCP key, owner, or DSH row onto the display name", () => {
    expect(SERVER_NAME).toBe("one-memory");
    expect(OWNER_VALUE).toBe("one-memory");
    expect(DSH_ROW_ID).toBe("one-memory-recall");
    expect(DSH_SERVER_NAME).toBe("onememory");
    // The skill is the one id hosts show by name, so it carries MemHub.
    const skill = renderSkillContent("mcp", runtime);
    expect(skill).toContain("name: memhub-recall");
    const servers = JSON.parse(renderConnectionMaterial(runtime).mcp)
      .mcpServers as Record<string, unknown>;
    expect(Object.keys(servers)).toEqual(["one-memory"]);
    expect(servers.memhub).toBeUndefined();
  });

  it("states the exact Chinese query notice once, and only for a real lookup", () => {
    const skill = renderSkillContent("mcp", runtime);
    expect(skill).toContain(QUERY_NOTICE);
    expect(skill).toContain("once per logical query");
    expect(skill).toContain("Loading this Skill is not a memory lookup");
    expect(skill).not.toContain("正在使用 MemHub 查看相关记忆");
    expect(skill).not.toContain("One Memory");
  });

  it("keeps the MCP server info name and puts MemHub only in the user-facing instructions", async () => {
    const session = new McpSession(runtime.discoveryPath);
    const initialized = JSON.parse(
      (await processMcpLine(
        session,
        JSON.stringify({
          jsonrpc: "2.0",
          id: 1,
          method: "initialize",
          params: {},
        }),
      )) ?? "",
    );
    expect(initialized.result.serverInfo.name).toBe("one-memory-recall");
    const listed = JSON.parse(
      (await processMcpLine(
        session,
        JSON.stringify({ jsonrpc: "2.0", id: 2, method: "tools/list" }),
      )) ?? "",
    );
    expect(
      listed.result.tools.map((tool: { name: string; title: string }) => [
        tool.name,
        tool.title,
      ]),
    ).toEqual([
      ["MemHub_recall", "MemHub recall"],
      ["MemHub_write", "MemHub write"],
      ["MemHub_diagnostic_trace", "MemHub diagnostic trace"],
    ]);
    expect(initialized.result.instructions).toContain(QUERY_NOTICE);
    expect(initialized.result.instructions).toContain("once per logical query");
    expect(initialized.result.instructions).not.toContain("One Memory");
  });

  it("does not derive the data directory or protocol from the display name", () => {
    const installer = readFileSync(
      new URL("./recall-installer.ts", import.meta.url),
      "utf8",
    );
    expect(installer).not.toMatch(/\.memhub/);
    expect(installer).not.toMatch(/memhub:\/\//);
    expect(installer).toContain('SERVER_NAME = "one-memory"');
  });
});
