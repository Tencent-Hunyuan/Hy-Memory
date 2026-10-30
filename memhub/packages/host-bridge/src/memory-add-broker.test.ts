// MemHub_write end to end: MCP session -> broker -> a fake Core. The memory is
// the same hand-added memory the app's 添加记忆 writes: authenticated by the
// host credential, not grant-governed, sent to Core as a trusted local caller
// (no external read envelope) and always filed cross-app.

import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { createServer, type IncomingHttpHeaders } from "node:http";
import type { AddressInfo } from "node:net";
import os from "node:os";
import path from "node:path";
import axios from "axios";
import { afterEach, describe, expect, it } from "vitest";

import {
  type BrokerClientOptions,
  memoryAddThroughBroker,
  readHostCredentialFile,
} from "./broker-client.js";
import { HostGrantStore } from "./host-grants.js";
import { McpSession } from "./mcp-session.js";
import { RecallBroker } from "./recall-broker.js";

const roots: string[] = [];
const closers: Array<() => Promise<void>> = [];
const coreToken = "isolated-core-only-token";
const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const REQUEST_ID = "5b0c4f0e-8a51-4c43-9a43-2f1f3f9c0d11";

afterEach(async () => {
  for (const close of closers.splice(0).reverse()) await close();
  await Promise.all(
    roots.splice(0).map((root) => rm(root, { recursive: true, force: true })),
  );
});

interface CoreCall {
  method: string | undefined;
  url: string | undefined;
  headers: IncomingHttpHeaders;
  body: unknown;
}

function receiptFor(text: string) {
  return {
    evidence_id: "ev-1",
    text,
    scope: "all-apps",
    created_at: "2026-10-27T00:00:00Z",
    state: "pending",
    task_id: null,
    memory_ids: [],
    error: null,
  };
}

async function fixture(options: { coreStatus?: number } = {}) {
  const root = await mkdtemp(path.join(os.tmpdir(), "om-memory-add-broker-"));
  roots.push(root);
  const store = await HostGrantStore.initialize(path.join(root, "grants"));
  const issued = await store.createInstallation({ hostId: "cursor" });
  // Only a raw-lane read is granted: a write must not depend on a read grant.
  await store.approveGrants({
    installationId: issued.receipt.installationId,
    expectedRevision: issued.receipt.revision,
    allowedTools: ["om_conversations"],
    allowedScopes: ["all-apps"],
  });
  const credentialFile = path.join(root, "host-credential");
  await writeFile(credentialFile, `${issued.secret}\n`, { mode: 0o600 });
  const calls: CoreCall[] = [];
  const core = createServer((request, response) => {
    void (async () => {
      const chunks: Buffer[] = [];
      for await (const chunk of request) chunks.push(Buffer.from(chunk));
      const raw = Buffer.concat(chunks).toString("utf8");
      const body = raw ? JSON.parse(raw) : undefined;
      calls.push({
        method: request.method,
        url: request.url,
        headers: request.headers,
        body,
      });
      response.setHeader("Content-Type", "application/json");
      if (request.url === "/api/v2/memories") {
        response.statusCode = options.coreStatus ?? 201;
        response.end(
          JSON.stringify(
            response.statusCode === 201
              ? receiptFor(body.text)
              : { detail: "refused" },
          ),
        );
        return;
      }
      response.statusCode = 404;
      response.end("{}");
    })();
  });
  await new Promise<void>((resolve) => core.listen(0, "127.0.0.1", resolve));
  closers.push(async () => {
    core.closeAllConnections();
    await new Promise<void>((resolve) => core.close(() => resolve()));
  });
  const discoveryPath = path.join(root, "broker.json");
  const broker = new RecallBroker({
    coreBaseUrl: `http://127.0.0.1:${(core.address() as AddressInfo).port}`,
    coreApiToken: coreToken,
    discoveryPath,
    instanceId: "isolated-memory-add-broker",
    preferredPort: 0,
    hostGrantStore: store,
  });
  await broker.start();
  closers.push(() => broker.stop());
  const client: BrokerClientOptions = {
    credentialProvider: () => readHostCredentialFile(credentialFile),
  };
  const session = new McpSession(
    discoveryPath,
    undefined,
    undefined,
    (discovery, request) => memoryAddThroughBroker(discovery, request, client),
    undefined,
    async () => [],
  );
  return {
    calls,
    discoveryPath,
    client,
    session,
    secret: issued.secret,
    baseUrl: `http://127.0.0.1:${broker.port}`,
  };
}

async function callWrite(session: McpSession, args: unknown) {
  return (await session.handle({
    jsonrpc: "2.0",
    id: 1,
    method: "tools/call",
    params: { name: "MemHub_write", arguments: args },
  })) as {
    result?: {
      isError?: boolean;
      structuredContent?: Record<string, unknown>;
      content: Array<{ text: string }>;
    };
    error?: { code: number; message: string };
  };
}

describe.skipIf(process.platform === "win32")("MemHub_write", () => {
  it("forwards {request_id, text, scope: all-apps} to Core as a trusted caller", async () => {
    const f = await fixture();
    const response = await callWrite(f.session, {
      text: "Prefers tabs over spaces",
      request_id: REQUEST_ID,
    });

    expect(response.result?.isError).toBeUndefined();
    expect(response.result?.structuredContent).toMatchObject({
      request_id: REQUEST_ID,
      evidence_id: "ev-1",
      state: "pending",
      text: "Prefers tabs over spaces",
    });
    expect(f.calls).toHaveLength(1);
    const [call] = f.calls;
    expect(call.method).toBe("POST");
    expect(call.url).toBe("/api/v2/memories");
    expect(call.body).toEqual({
      request_id: REQUEST_ID,
      text: "Prefers tabs over spaces",
      scope: "all-apps",
    });
    expect(call.headers.authorization).toBe(`Bearer ${coreToken}`);
    // Core denies POST /api/v2/memories to enveloped requests: none of it may leak.
    for (const header of Object.keys(call.headers)) {
      expect(header).not.toMatch(
        /^x-one-memory-(read-context|host-id|installation-|allowed-)/,
      );
    }
  });

  it("generates a UUID v4 request_id when the model gives none", async () => {
    const f = await fixture();
    const response = await callWrite(f.session, { text: "Ships on Fridays" });
    const sent = f.calls[0].body as { request_id: string };
    expect(sent.request_id).toMatch(UUID_V4);
    expect(response.result?.structuredContent?.request_id).toBe(
      sent.request_id,
    );
  });

  it("maps a 409 conflict and a 422 refusal to their own errors", async () => {
    const conflict = await fixture({ coreStatus: 409 });
    await expect(
      memoryAddThroughBroker(
        conflict.discoveryPath,
        { request_id: REQUEST_ID, text: "a" },
        conflict.client,
      ),
    ).rejects.toMatchObject({ code: "memory_add_conflict" });
    const conflictCall = await callWrite(conflict.session, {
      text: "a",
      request_id: REQUEST_ID,
    });
    expect(conflictCall.result?.isError).toBe(true);

    const invalid = await fixture({ coreStatus: 422 });
    await expect(
      memoryAddThroughBroker(
        invalid.discoveryPath,
        { request_id: REQUEST_ID, text: "a" },
        invalid.client,
      ),
    ).rejects.toMatchObject({ code: "memory_add_invalid" });
  });

  it("refuses an unauthenticated write before reaching Core", async () => {
    const f = await fixture();
    const response = await axios.post(
      `${f.baseUrl}/memories`,
      { request_id: REQUEST_ID, text: "anything" },
      {
        headers: { "Content-Type": "application/json" },
        proxy: false,
        validateStatus: () => true,
      },
    );
    expect(response.status).toBe(401);
    expect(f.calls).toHaveLength(0);
  });

  it("refuses bodies that name a scope or source, or carry blank text", async () => {
    const f = await fixture();
    for (const body of [
      { request_id: REQUEST_ID, text: "x", scope: "all-apps" },
      { request_id: REQUEST_ID, text: "x", source_id: "codex" },
      { request_id: "not-a-uuid", text: "x" },
      { request_id: REQUEST_ID, text: "   " },
      { request_id: REQUEST_ID, text: "x".repeat(12_001) },
      { text: "x" },
    ]) {
      const response = await axios.post(`${f.baseUrl}/memories`, body, {
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${f.secret}`,
        },
        proxy: false,
        validateStatus: () => true,
      });
      expect(response.status).toBe(400);
    }
    expect(f.calls).toHaveLength(0);
  });

  it("rejects invalid tool arguments without calling the broker", async () => {
    const f = await fixture();
    for (const args of [
      {},
      { text: "" },
      { text: "  \n " },
      { text: "x", request_id: "req-1" },
      { text: "x", scope: "all-apps" },
      { messages: [{ role: "user", content: "x" }] },
    ]) {
      const response = await callWrite(f.session, args);
      expect(response.error?.code).toBe(-32602);
    }
    expect(f.calls).toHaveLength(0);
  });
});
