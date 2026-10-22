import { mkdtemp } from "node:fs/promises";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import os from "node:os";
import path from "node:path";
import axios from "axios";
import { afterEach, describe, expect, it } from "vitest";

import { RecallBroker } from "./recall-broker.js";

const closers: Array<() => Promise<void>> = [];

afterEach(async () => {
  await Promise.allSettled(closers.splice(0).map((close) => close()));
});

describe("RecallBroker /ask", () => {
  it("proxies ask to Core and returns empty refuse verbatim", async () => {
    let authorization: string | undefined;
    let readContext: string | string[] | undefined;
    let coreBody: unknown;
    const core = createServer(async (request, response) => {
      if (
        request.method === "GET" &&
        request.url === "/api/v2/memory-authority"
      ) {
        response.setHeader("Content-Type", "application/json");
        response.end(
          JSON.stringify({ authority: "legacy", migration_id: null }),
        );
        return;
      }
      authorization = request.headers.authorization;
      readContext = request.headers["x-one-memory-read-context"];
      if (request.url !== "/api/v1/memories/ask") {
        response.statusCode = 404;
        response.end("not found");
        return;
      }
      const chunks: Buffer[] = [];
      request.on("data", (chunk) => chunks.push(chunk));
      request.on("end", () => {
        coreBody = JSON.parse(Buffer.concat(chunks).toString("utf8"));
        response.setHeader("Content-Type", "application/json");
        response.end(
          JSON.stringify({
            answer: "现有记忆不足以回答这个问题。",
            memories: [],
            mode: "empty",
          }),
        );
      });
    });
    const corePort = await listen(core, 0);
    closers.push(() => close(core));
    const root = await mkdtemp(path.join(os.tmpdir(), "om-ask-broker-"));
    const discoveryPath = path.join(root, "cursor-broker.json");
    const broker = new RecallBroker({
      coreBaseUrl: `http://127.0.0.1:${corePort}`,
      coreApiToken: "master-secret",
      discoveryPath,
      instanceId: "ask-instance",
      preferredPort: 0,
    });
    await broker.start();
    closers.push(() => broker.stop());

    const response = await axios.post(
      `http://127.0.0.1:${broker.port}/ask`,
      {
        question: "我下周的航班是什么？",
        scope: "all-apps",
        workspace_path: "/tmp/project",
      },
      {
        headers: {
          "Content-Type": "application/json",
          "X-One-Memory-Client": "cursor-mcp",
        },
      },
    );

    expect(authorization).toBe("Bearer master-secret");
    expect(readContext).toBe("external");
    expect(coreBody).toEqual({
      question: "我下周的航班是什么？",
      scope: "all-apps",
      workspace_path: "/tmp/project",
    });
    expect(response.data).toEqual({
      answer: "现有记忆不足以回答这个问题。",
      memories: [],
      mode: "empty",
    });
  });

  it("selects SDK Ask v2 after cutover without Governance fields", async () => {
    const calls: string[] = [];
    const core = createServer(async (request, response) => {
      calls.push(request.url ?? "");
      response.setHeader("Content-Type", "application/json");
      if (request.url === "/api/v2/memory-authority") {
        response.end(
          JSON.stringify({ authority: "sdk", migration_id: "migration-1" }),
        );
        return;
      }
      if (request.url === "/api/v2/memories/ask") {
        for await (const _chunk of request) void _chunk;
        response.end(
          JSON.stringify({
            schema_version: 2,
            answer: "你偏好本地优先。",
            memories: [
              {
                memory_id: "memory-1",
                content: "用户偏好本地优先。",
                layer: "l2_fact",
                status: "active",
                source: "cursor",
                gmt_created: null,
              },
            ],
            mode: "extractive",
          }),
        );
        return;
      }
      response.statusCode = 404;
      response.end("not found");
    });
    const corePort = await listen(core, 0);
    closers.push(() => close(core));
    const root = await mkdtemp(path.join(os.tmpdir(), "om-ask-sdk-"));
    const broker = new RecallBroker({
      coreBaseUrl: `http://127.0.0.1:${corePort}`,
      coreApiToken: "master-secret",
      discoveryPath: path.join(root, "broker.json"),
      instanceId: "ask-sdk",
      preferredPort: 0,
    });
    await broker.start();
    closers.push(() => broker.stop());

    const response = await axios.post(
      `http://127.0.0.1:${broker.port}/ask`,
      { question: "我的偏好？" },
      { headers: { "X-One-Memory-Client": "cursor-mcp" } },
    );

    expect(calls).toEqual(["/api/v2/memory-authority", "/api/v2/memories/ask"]);
    expect(response.data.schema_version).toBe(2);
    expect(JSON.stringify(response.data)).not.toMatch(
      /revision|evidence_ids|scopes/,
    );
  });
});

function listen(
  server: ReturnType<typeof createServer>,
  port: number,
): Promise<number> {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", () => {
      resolve((server.address() as AddressInfo).port);
    });
  });
}

function close(server: ReturnType<typeof createServer>): Promise<void> {
  return new Promise((resolve, reject) => {
    server.close((error) => {
      if (error) reject(error);
      else resolve();
    });
  });
}
