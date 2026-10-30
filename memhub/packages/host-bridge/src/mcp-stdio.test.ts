import { spawn } from "node:child_process";
import path from "node:path";
import { describe, expect, it } from "vitest";

describe("built MCP stdio server", () => {
  it("speaks newline-delimited JSON-RPC over stdio", async () => {
    const child = spawn(
      process.execPath,
      [path.join(process.cwd(), "dist", "mcp.cjs")],
      {
        stdio: ["pipe", "pipe", "pipe"],
      },
    );
    const output = collectLines(child, 2);
    child.stdin.write(
      `${JSON.stringify({
        jsonrpc: "2.0",
        id: 1,
        method: "initialize",
        params: { protocolVersion: "2025-03-26" },
      })}\n`,
    );
    child.stdin.write(
      `${JSON.stringify({
        jsonrpc: "2.0",
        id: 2,
        method: "tools/list",
      })}\n`,
    );

    const lines = await output;
    child.kill();
    const responses = lines.map((line) => JSON.parse(line));
    expect(responses.find((item) => item.id === 1).result.serverInfo.name).toBe(
      "one-memory-recall",
    );
    expect(
      responses
        .find((item) => item.id === 2)
        .result.tools.map((tool: { name: string }) => tool.name),
    ).toEqual(["MemHub_recall", "MemHub_write", "MemHub_diagnostic_trace"]);
    // Spawning Node and loading the bundle competes with whatever else the
    // machine is doing, so the budget has to cover a busy machine; the
    // collector's own timeout below is what reports a server that never
    // answers.
  }, 30_000);
});

function collectLines(
  child: ReturnType<typeof spawn>,
  count: number,
): Promise<string[]> {
  return new Promise((resolve, reject) => {
    const lines: string[] = [];
    let buffered = "";
    const timeout = setTimeout(() => {
      child.kill();
      reject(new Error("Timed out waiting for MCP stdio output"));
    }, 20_000);
    child.once("error", reject);
    child.stdout?.setEncoding("utf8");
    child.stdout?.on("data", (chunk: string) => {
      buffered += chunk;
      const parts = buffered.split("\n");
      buffered = parts.pop() ?? "";
      lines.push(...parts.filter(Boolean));
      if (lines.length >= count) {
        clearTimeout(timeout);
        resolve(lines.slice(0, count));
      }
    });
  });
}
