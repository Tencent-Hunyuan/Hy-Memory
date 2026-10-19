import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { createServer, type IncomingHttpHeaders } from "node:http";
import type { AddressInfo } from "node:net";
import os from "node:os";
import path from "node:path";
import axios from "axios";
import { afterEach, expect, it, vi } from "vitest";
import {
  contextThroughBroker,
  recallThroughBroker,
  reportCallEventDeliveryThroughBroker,
} from "./broker-client.js";
import {
  CONTEXT_INPUT_SCHEMA,
  contextResponseInvalidField,
  HOST_CONTEXT_TIMEOUT_MS,
  isContextSearchResponse,
  isGovernedMemoryProvenance,
} from "./context-protocol.js";
import { HostGrantStore, type HostReadTool } from "./host-grants.js";
import { McpSession } from "./mcp-session.js";
import { RecallBroker } from "./recall-broker.js";
import { runRecallCli } from "./recall-cli.js";
import {
  piLayout,
  readInstalledRecallCommand,
  renderSkillContent,
} from "./recall-installer.js";

const cleanups: Array<() => Promise<void>> = [];
afterEach(async () => {
  for (const cleanup of cleanups.splice(0).reverse()) await cleanup();
});
const lane = {
  status: "empty",
  reason: null,
  elapsed_ms: 0,
  returned_count: 0,
  truncated: false,
  truncated_by: [],
};
const empty = {
  schema_version: 1,
  request_id: "fixture-request",
  state: "empty",
  paths: { raw: lane, memory: lane },
  items: [],
  used_characters: 0,
  truncated: false,
  total_ms: 1,
  adoption: "unknown",
};
const raw = {
  kind: "raw",
  source_id: "codex",
  conversation_id: "fixture-conversation",
  anchor_evidence_id: "e1",
  messages: [
    {
      evidence_id: "e1",
      revision: 1,
      role: "user",
      written_by: "person",
      created_at: null,
      text: "original",
      truncated: false,
    },
  ],
  truncated: false,
};
const memory = {
  kind: "memory",
  memory_id: "m1",
  revision: 1,
  content: "memory",
  memory_kind: "chat",
  updated_at: "2026-09-19T00:00:00Z",
  evidence_ids: ["e1"],
  matched_by: ["lexical"],
  truncated: false,
};
const mixed = {
  ...empty,
  state: "grounded",
  paths: {
    raw: { ...lane, status: "ok", returned_count: 1 },
    memory: { ...lane, status: "ok", returned_count: 1 },
  },
  items: [raw, memory],
  used_characters: 14,
};

it("requires the governed-memory match provenance field", () => {
  const withoutProvenance = {
    ...mixed,
    items: [{ ...memory, matched_by: undefined }],
  };
  expect(isContextSearchResponse(mixed)).toBe(true);
  expect(isContextSearchResponse(withoutProvenance)).toBe(false);
  expect(isGovernedMemoryProvenance(["lexical"])).toBe(true);
  expect(isGovernedMemoryProvenance(["governed_semantic"])).toBe(true);
  expect(isGovernedMemoryProvenance(["lexical", "governed_semantic"])).toBe(
    true,
  );
  for (const invalid of [
    undefined,
    [],
    ["unknown"],
    ["lexical", "lexical"],
    ["lexical", "governed_semantic", "lexical"],
  ]) {
    expect(isGovernedMemoryProvenance(invalid)).toBe(false);
  }
});

it("identifies an invalid governed-memory provenance field without exposing content", () => {
  const response = receiptFor([{ ...memory, matched_by: ["semantic"] }]);
  expect(contextResponseInvalidField(response)).toBe("items[0].matched_by");
});

async function fixture(
  tools: HostReadTool[] = ["om_recall", "om_conversations"],
  scopes = ["all-apps", "source:codex"],
  noStore = false,
  hostId = "codex",
  now?: () => number,
) {
  const root = await mkdtemp(path.join(os.tmpdir(), "om-context-"));
  cleanups.push(() => rm(root, { recursive: true, force: true }));
  const store = await HostGrantStore.initialize(path.join(root, "grants"));
  const issued = await store.createInstallation({ hostId });
  const grant = tools.length
    ? await store.approveGrants({
        installationId: issued.receipt.installationId,
        expectedRevision: 1,
        allowedTools: tools,
        allowedScopes: scopes,
      })
    : issued.receipt;
  const calls: Array<{
    url: string | undefined;
    body: unknown;
    headers: IncomingHttpHeaders;
  }> = [];
  const diagnostics: Array<Record<string, unknown>> = [];
  let status = 200;
  let result: unknown = empty;
  let beforeReply: () => Promise<void> = async () => {};
  let disconnected = false;
  const core = createServer(async (request, response) => {
    response.on("close", () => {
      if (!response.writableEnded) disconnected = true;
    });
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    calls.push({
      url: request.url,
      body: JSON.parse(Buffer.concat(chunks).toString()),
      headers: request.headers,
    });
    await beforeReply();
    response.writeHead(status, { "content-type": "application/json" });
    response.end(JSON.stringify(result));
  });
  await new Promise<void>((resolve) => core.listen(0, "127.0.0.1", resolve));
  cleanups.push(
    () =>
      new Promise<void>((resolve) => {
        core.closeAllConnections();
        core.close(() => resolve());
      }),
  );
  const discoveryPath = path.join(root, "discovery.json");
  const broker = new RecallBroker({
    coreBaseUrl: `http://127.0.0.1:${(core.address() as AddressInfo).port}`,
    coreApiToken: "fixture-core-secret",
    discoveryPath,
    instanceId: "fixture-instance",
    preferredPort: 0,
    ...(now ? { now } : {}),
    ...(!noStore ? { hostGrantStore: store } : {}),
    diagnostics: async (event) => void diagnostics.push(event),
  });
  await broker.start();
  cleanups.push(() => broker.stop());
  const post = (
    body: unknown = { query: "original" },
    secret: string | null = issued.secret,
  ) =>
    axios.post(`http://127.0.0.1:${broker.port}/context`, body, {
      proxy: false,
      timeout: 3000,
      validateStatus: () => true,
      headers: {
        "content-type": "application/json",
        "x-one-memory-client": "cursor-mcp",
        ...(secret ? { Authorization: `Bearer ${secret}` } : {}),
      },
    });
  return {
    disconnected: () => disconnected,
    store,
    issued,
    grant,
    calls,
    diagnostics,
    post,
    discoveryPath,
    respond(value: unknown, code = 200) {
      result = value;
      status = code;
    },
    before(action: () => Promise<void>) {
      beforeReply = action;
    },
  };
}

it("logs the invalid Core field when legacy semantic provenance causes a 502", async () => {
  const f = await fixture(["om_recall"], ["all-apps"]);
  f.respond(receiptFor([{ ...memory, matched_by: ["semantic"] }]));

  const result = await f.post();

  expect(result.status).toBe(502);
  expect(f.diagnostics).toContainEqual(
    expect.objectContaining({
      event: "context.response_invalid",
      error_code: "invalid_context_response",
      details: { reason: "items[0].matched_by" },
    }),
  );
  expect(JSON.stringify(f.diagnostics)).not.toContain(memory.content);
});

it.each(["codex", "cursor", "workbuddy", "claude-code"])(
  "passes a governed semantic Core response through to %s",
  async (hostId) => {
    const f = await fixture(["om_recall"], ["all-apps"], false, hostId);
    const response = receiptFor([
      { ...memory, matched_by: ["governed_semantic"] },
    ]);
    f.respond(response);

    const result = await f.post();

    expect(result.status).toBe(200);
    expect(result.data).toEqual(response);
  },
);

it("binds a one-shot WorkBuddy delivery report to the context installation", async () => {
  const f = await fixture(["om_recall"], ["all-apps"], false, "workbuddy");
  const requestId = "11111111-1111-4111-8111-111111111111";
  f.respond({ ...empty, request_id: requestId });
  await expect(
    contextThroughBroker(
      f.discoveryPath,
      { query: "original" },
      { credentialProvider: () => f.issued.secret },
    ),
  ).resolves.toMatchObject({ request_id: requestId });

  const other = await f.store.createInstallation({ hostId: "codex" });
  await expect(
    reportCallEventDeliveryThroughBroker(
      f.discoveryPath,
      {
        request_id: requestId,
        delivery: "delivered",
        host_session_id: "session-1",
        injection_elapsed_ms: 42,
      },
      { credentialProvider: () => other.secret },
    ),
  ).rejects.toMatchObject({ code: "host_authorization_required" });
  expect(f.calls).toHaveLength(1);

  f.respond({ delivery: "delivered", adoption: "unknown" });
  await expect(
    reportCallEventDeliveryThroughBroker(
      f.discoveryPath,
      {
        request_id: requestId,
        delivery: "delivered",
        host_session_id: "session-1",
        injection_elapsed_ms: 42,
      },
      { credentialProvider: () => f.issued.secret },
    ),
  ).resolves.toEqual({ delivery: "delivered", adoption: "unknown" });
  expect(f.calls.at(-1)).toMatchObject({
    url: `/api/v1/call-events/${requestId}/delivery`,
    body: {
      delivery: "delivered",
      host_session_id: "session-1",
      injection_elapsed_ms: 42,
    },
  });
  expect(f.calls.at(-1)?.headers).toMatchObject({
    "x-one-memory-read-context": "external",
    "x-one-memory-host-id": "workbuddy",
    "x-one-memory-installation-id": f.issued.receipt.installationId,
    "x-one-memory-installation-revision": String(f.grant.revision),
    "x-one-memory-allowed-scopes": JSON.stringify(f.grant.allowedScopes),
    "x-one-memory-allowed-tools": JSON.stringify(f.grant.allowedTools),
  });
  await expect(
    reportCallEventDeliveryThroughBroker(
      f.discoveryPath,
      {
        request_id: requestId,
        delivery: "delivered",
        host_session_id: "session-1",
        injection_elapsed_ms: 42,
      },
      { credentialProvider: () => f.issued.secret },
    ),
  ).rejects.toMatchObject({ code: "host_authorization_required" });
});

it("expires an unused WorkBuddy delivery binding", async () => {
  let now = 1_000;
  const f = await fixture(
    ["om_recall"],
    ["all-apps"],
    false,
    "workbuddy",
    () => now,
  );
  const requestId = "22222222-2222-4222-8222-222222222222";
  f.respond({ ...empty, request_id: requestId });
  await contextThroughBroker(
    f.discoveryPath,
    { query: "original" },
    { credentialProvider: () => f.issued.secret },
  );
  now += 60_001;
  await expect(
    reportCallEventDeliveryThroughBroker(
      f.discoveryPath,
      {
        request_id: requestId,
        delivery: "delivered",
        host_session_id: "session-1",
        injection_elapsed_ms: 42,
      },
      { credentialProvider: () => f.issued.secret },
    ),
  ).rejects.toMatchObject({ code: "host_authorization_required" });
  expect(f.calls).toHaveLength(1);
});

it("rechecks the WorkBuddy installation after Core records delivery", async () => {
  const f = await fixture(["om_recall"], ["all-apps"], false, "workbuddy");
  const requestId = "33333333-3333-4333-8333-333333333333";
  f.respond({ ...empty, request_id: requestId });
  await contextThroughBroker(
    f.discoveryPath,
    { query: "original" },
    { credentialProvider: () => f.issued.secret },
  );
  f.respond({ delivery: "delivered", adoption: "unknown" });
  f.before(async () => {
    await f.store.revoke({
      installationId: f.issued.receipt.installationId,
      expectedRevision: f.grant.revision,
    });
  });
  await expect(
    reportCallEventDeliveryThroughBroker(
      f.discoveryPath,
      {
        request_id: requestId,
        delivery: "delivered",
        host_session_id: "session-1",
        injection_elapsed_ms: 42,
      },
      { credentialProvider: () => f.issued.secret },
    ),
  ).rejects.toMatchObject({ code: "host_authorization_required" });
  expect(f.calls.at(-1)?.url).toBe(`/api/v1/call-events/${requestId}/delivery`);
});

it.each([
  { tools: ["om_recall"], scopes: ["all-apps"] },
  { tools: ["om_recall"], scopes: [`workspace:v1:${"a".repeat(64)}`] },
  { tools: ["om_conversations"], scopes: ["all-apps", "source:codex"] },
])(
  "combines only the underlying grants: $tools $scopes",
  async ({ tools, scopes }) => {
    const f = await fixture(tools as HostReadTool[], scopes);
    expect((await f.post()).status).toBe(200);
    expect(f.calls).toHaveLength(1);
    expect(f.calls[0].url).toBe("/api/v1/context/search");
    expect(f.calls[0].body).toEqual({ query: "original" });
    expect(
      JSON.parse(f.calls[0].headers["x-one-memory-allowed-tools"] as string),
    ).toEqual(tools);
    expect(
      JSON.parse(f.calls[0].headers["x-one-memory-allowed-scopes"] as string),
    ).toEqual(scopes);
  },
);

it("uses the fixed generic identity for raw and governed memory without per-source grants", async () => {
  const f = await fixture(
    ["om_recall", "om_conversations"],
    ["all-apps", "raw-imported-all"],
    false,
    "generic-local-mcp",
  );
  f.respond(mixed);
  const result = await f.post({ query: "original", raw_source_ids: ["codex"] });
  expect(result.status).toBe(200);
  expect(result.data.items).toEqual([raw, memory]);
  const defaultResult = await f.post();
  expect(defaultResult.status).toBe(200);
  expect(defaultResult.data.items).toEqual([raw, memory]);
  expect(f.calls).toHaveLength(2);
  expect(f.calls[0].headers["x-one-memory-host-id"]).toBe("generic-local-mcp");
  expect(
    JSON.parse(f.calls[0].headers["x-one-memory-allowed-scopes"] as string),
  ).toEqual(["all-apps", "raw-imported-all"]);
});

it("keeps Core's active-source denial for an explicit generic raw source", async () => {
  const f = await fixture(
    ["om_recall", "om_conversations"],
    ["all-apps", "raw-imported-all"],
    false,
    "generic-local-mcp",
  );
  f.respond({ detail: "inactive source" }, 403);
  const result = await f.post({
    query: "original",
    raw_source_ids: ["inactive"],
  });
  expect(result.status).toBe(403);
  expect(JSON.stringify(result.data)).not.toContain("inactive source");
  expect(f.calls).toHaveLength(1);
  expect(f.calls[0].body).toEqual({
    query: "original",
    raw_source_ids: ["inactive"],
  });
});

it("never releases a generic raw item outside explicitly requested sources", async () => {
  const f = await fixture(
    ["om_recall", "om_conversations"],
    ["all-apps", "raw-imported-all"],
    false,
    "generic-local-mcp",
  );
  f.respond(receiptFor([{ ...raw, source_id: "cursor" }]));
  const result = await f.post({ query: "original", raw_source_ids: ["codex"] });
  expect(result.status).toBe(502);
  expect(JSON.stringify(result.data)).not.toContain("original");
});

it.each([
  { tools: [], scopes: ["all-apps"], body: { query: "x" } },
  { tools: ["om_ask"], scopes: ["all-apps"], body: { query: "x" } },
  { tools: ["om_recall"], scopes: ["source:codex"], body: { query: "x" } },
  { tools: ["om_conversations"], scopes: ["all-apps"], body: { query: "x" } },
  {
    tools: ["om_recall"],
    scopes: ["all-apps"],
    body: { query: "x", kinds: ["raw"] },
  },
  {
    tools: ["om_recall", "om_conversations"],
    scopes: ["all-apps", "source:codex"],
    body: { query: "x", raw_source_ids: ["cursor"] },
  },
])(
  "denies invalid requested authority before Core: $tools $body",
  async ({ tools, scopes, body }) => {
    const f = await fixture(tools as HostReadTool[], scopes);
    expect((await f.post(body)).status).toBe(403);
    expect(f.calls).toEqual([]);
  },
);

it.each([
  { query: " " },
  { query: "x", scope: "*" },
  { query: "x", workspace_path: "/private" },
  { query: "x", source_ids: ["codex"] },
  { query: "x", kinds: ["raw", "raw"] },
  { query: "x", kinds: ["memory"], raw_source_ids: ["codex"] },
  { query: "x", raw_source_ids: [] },
  { query: "x", raw_source_ids: ["codex", "codex"] },
  { query: "x", limit: true },
  { query: "x", limit: 21 },
  { query: "x", timeout_ms: 49 },
  { query: "x", character_budget: 20001 },
  { query: "x", installation_id: "forged" },
])("rejects invalid request %j without a downstream read", async (body) => {
  const f = await fixture();
  expect((await f.post(body)).status).toBe(400);
  expect(f.calls).toEqual([]);
});

it("requires a grant store, including legacy standalone mode", async () => {
  const f = await fixture(undefined, undefined, true);
  expect((await f.post()).status).toBe(503);
  expect(f.calls).toEqual([]);
});

it("rejects absent credentials and does not echo secrets", async () => {
  const f = await fixture();
  const response = await f.post(undefined, null);
  expect(response.status).toBe(401);
  expect(JSON.stringify(response.data)).not.toContain(f.issued.secret);
  expect(f.calls).toEqual([]);
});

it.each(["revoke", "rotate", "narrow"])(
  "blocks release when installation changes: %s",
  async (mode) => {
    const f = await fixture();
    f.respond(mixed);
    f.before(async () => {
      const mutation = {
        installationId: f.grant.installationId,
        expectedRevision: f.grant.revision,
      };
      if (mode === "revoke") await f.store.revoke(mutation);
      else if (mode === "rotate") await f.store.rotateSecret(mutation);
      else
        await f.store.approveGrants({
          ...mutation,
          allowedTools: ["om_recall"],
          allowedScopes: ["all-apps"],
        });
    });
    const response = await f.post();
    expect([401, 403]).toContain(response.status);
    expect(JSON.stringify(response.data)).not.toContain("original");
    expect(f.calls).toHaveLength(1);
  },
);

it.each([404, 405, 501])(
  "old Core %i reports upgrade, with no legacy fallback",
  async (status) => {
    const f = await fixture();
    f.respond({ detail: "secret backend details" }, status);
    await expect(
      contextThroughBroker(
        f.discoveryPath,
        { query: "x" },
        { credentialProvider: () => f.issued.secret },
      ),
    ).rejects.toMatchObject({ code: "context_upgrade_required" });
    expect(f.calls.map((call) => call.url)).toEqual(["/api/v1/context/search"]);
  },
);

it.each([403, 503])(
  "Core %i remains a failure and never triggers fallback",
  async (status) => {
    const f = await fixture();
    f.respond({ detail: f.issued.secret }, status);
    const response = await f.post();
    expect(response.status).toBe(status === 403 ? 403 : 502);
    expect(JSON.stringify(response.data)).not.toContain(f.issued.secret);
    expect(f.calls).toHaveLength(1);
  },
);

it("reports a Core context failure without claiming MemHub is stopped", async () => {
  const f = await fixture();
  f.respond({ error: "context_unavailable" }, 502);

  await expect(
    contextThroughBroker(
      f.discoveryPath,
      { query: "x" },
      { credentialProvider: () => f.issued.secret },
    ),
  ).rejects.toMatchObject({
    code: "context_unavailable",
    message:
      "MemHub is running, but context search is temporarily unavailable.",
  });
  expect(f.calls.map((call) => call.url)).toEqual(["/api/v1/context/search"]);
});

it("MCP advertises context and passes the exact mixed receipt without workspace injection", async () => {
  const f = await fixture();
  f.respond(mixed);
  const legacy = vi.fn();
  const session = new McpSession(
    f.discoveryPath,
    legacy,
    "/must-not-inject",
    legacy,
    legacy,
    legacy,
    (discovery, request) =>
      contextThroughBroker(discovery, request, {
        credentialProvider: () => f.issued.secret,
      }),
  );
  const listed = await session.handle({
    jsonrpc: "2.0",
    id: 1,
    method: "tools/list",
  });
  expect(listed?.result).toMatchObject({
    tools: expect.arrayContaining([
      expect.objectContaining({ name: "MemHub_context" }),
    ]),
  });
  const response = await session.handle({
    jsonrpc: "2.0",
    id: 2,
    method: "tools/call",
    params: {
      name: "om_context",
      arguments: { query: "original", raw_source_ids: ["codex"] },
    },
  });
  expect(response?.result).toEqual({
    content: [{ type: "text", text: JSON.stringify(mixed) }],
    structuredContent: mixed,
  });
  expect(f.calls[0].body).toEqual({
    query: "original",
    raw_source_ids: ["codex"],
    timeout_ms: HOST_CONTEXT_TIMEOUT_MS,
  });
  expect(legacy).not.toHaveBeenCalled();
});

it("CLI context mode uses its credential and preserves JSON and prose provenance", async () => {
  const f = await fixture();
  f.respond(mixed);
  // Credential provider is the same on-disk path used by the shipped executable.
  const { mkdir, writeFile } = await import("node:fs/promises");
  const directory = `${f.discoveryPath}.credential`;
  await mkdir(directory, { mode: 0o700 });
  const file = path.join(directory, "credential");
  await writeFile(file, f.issued.secret, { mode: 0o600 });
  const env = {
    ONE_MEMORY_BROKER_DISCOVERY: f.discoveryPath,
    ONE_MEMORY_HOST_CREDENTIAL_FILE: file,
  };
  const result = await runRecallCli(
    ["--context", "original", "--raw-source", "codex", "--json"],
    env,
  );
  expect(result.exitCode).toBe(0);
  expect(JSON.parse(result.stdout)).toEqual(mixed);
  const prose = await runRecallCli(["--context", "original"], env);
  expect(prose.stdout).toContain("e1");
  expect(prose.stdout).toContain("m1");
  expect(prose.stdout).toContain("codex");
  expect(prose.stdout).toContain("unknown");
  expect(f.calls.every((call) => call.url === "/api/v1/context/search")).toBe(
    true,
  );
});

it("rejects malformed Core success rather than exposing arbitrary payload", async () => {
  const f = await fixture();
  f.respond({ secret: "do-not-expose" });
  const response = await f.post();
  expect(response.status).toBe(502);
  expect(JSON.stringify(response.data)).not.toContain("do-not-expose");
});

it("cancels an in-flight HTTP read and never releases its late content", async () => {
  const f = await fixture();
  f.respond(mixed);
  let release!: () => void;
  f.before(
    () =>
      new Promise<void>((resolve) => {
        release = resolve;
      }),
  );
  const controller = new AbortController();
  const result = contextThroughBroker(
    f.discoveryPath,
    { query: "original" },
    {
      credentialProvider: () => f.issued.secret,
      signal: controller.signal,
    },
  ).then(
    () => "unexpected content",
    (error: unknown) => error,
  );
  try {
    await vi.waitFor(() => expect(f.calls).toHaveLength(1));
    controller.abort();
    await expect(result).resolves.toMatchObject({ code: "context_cancelled" });
    await vi.waitFor(() => expect(f.disconnected()).toBe(true));
    expect(f.calls).toHaveLength(1);
  } finally {
    release?.();
    await result;
  }
});

it("MCP cancellation reaches the HTTP request and only affects its own id", async () => {
  const f = await fixture();
  f.respond(mixed);
  let release!: () => void;
  f.before(
    () =>
      new Promise<void>((resolve) => {
        release = resolve;
      }),
  );
  const session = new McpSession(
    f.discoveryPath,
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    (discovery, request, signal) =>
      contextThroughBroker(discovery, request, {
        credentialProvider: () => f.issued.secret,
        signal,
      }),
  );
  const pending = session.handle({
    jsonrpc: "2.0",
    id: 41,
    method: "tools/call",
    params: { name: "om_context", arguments: { query: "original" } },
  });
  try {
    await vi.waitFor(() => expect(f.calls).toHaveLength(1));
    await session.handle({
      jsonrpc: "2.0",
      method: "notifications/cancelled",
      params: { requestId: 42 },
    });
    expect(f.disconnected()).toBe(false);
    await session.handle({
      jsonrpc: "2.0",
      method: "notifications/cancelled",
      params: { requestId: 41 },
    });
    await expect(pending).resolves.toMatchObject({ result: { isError: true } });
    expect(JSON.stringify(await pending)).toContain("cancelled");
    await vi.waitFor(() => expect(f.disconnected()).toBe(true));
    expect(JSON.stringify(await pending)).not.toContain("original");
  } finally {
    release?.();
    await pending;
  }
});

it("cancellation while resolving a credential never dispatches a late request", async () => {
  const f = await fixture();
  let resolveCredential!: (value: string) => void;
  const credential = new Promise<string>((resolve) => {
    resolveCredential = resolve;
  });
  const controller = new AbortController();
  const pending = contextThroughBroker(
    f.discoveryPath,
    { query: "x" },
    { credentialProvider: () => credential, signal: controller.signal },
  ).then(
    () => "unexpected content",
    (error: unknown) => error,
  );
  controller.abort();
  try {
    await expect(
      Promise.race([
        pending,
        new Promise((resolve) =>
          setTimeout(() => resolve("cancel not observed"), 100),
        ),
      ]),
    ).resolves.toMatchObject({ code: "context_cancelled" });
  } finally {
    resolveCredential(f.issued.secret);
    await pending;
  }
  expect(f.calls).toEqual([]);
});

it.each([
  { query: "x", limit: 1 },
  { query: "x", kinds: ["raw"] },
  { query: "x", raw_source_ids: ["cursor"] },
])(
  "rejects a valid-shaped response outside the requested selection %j",
  async (body) => {
    const f = await fixture(undefined, [
      "all-apps",
      "source:codex",
      "source:cursor",
    ]);
    f.respond(mixed);
    expect((await f.post(body)).status).toBe(502);
  },
);

async function nativeEnvironment(f: Awaited<ReturnType<typeof fixture>>) {
  const { mkdir, writeFile } = await import("node:fs/promises");
  const directory = `${f.discoveryPath}.native-credential`;
  await mkdir(directory, { mode: 0o700 });
  const credentialFile = path.join(directory, "credential");
  await writeFile(credentialFile, f.issued.secret, { mode: 0o600 });
  return {
    ...process.env,
    ONE_MEMORY_BROKER_DISCOVERY: f.discoveryPath,
    ONE_MEMORY_HOST_CREDENTIAL_FILE: credentialFile,
  };
}

function nativeCli(args: string[], env: NodeJS.ProcessEnv) {
  return new Promise<{ stdout: string; code: number | null }>(
    (resolve, reject) => {
      const child = spawn(
        process.execPath,
        [path.resolve("dist/recall-cli.cjs"), ...args],
        { env, stdio: ["ignore", "pipe", "pipe"] },
      );
      let stdout = "";
      const timer = setTimeout(() => {
        child.kill();
        reject(new Error("fixture CLI timeout"));
      }, 6000);
      child.stdout.on("data", (chunk) => {
        stdout += chunk;
      });
      child.on("error", (error) => {
        clearTimeout(timer);
        reject(error);
      });
      child.on("close", (code) => {
        clearTimeout(timer);
        resolve({ stdout, code });
      });
    },
  );
}

it("shipped CLI bundle performs authenticated raw+memory lookup", async () => {
  const f = await fixture();
  f.respond(mixed);
  const result = await nativeCli(
    ["--context", "original", "--json"],
    await nativeEnvironment(f),
  );
  expect(result.code).toBe(0);
  expect(JSON.parse(result.stdout)).toEqual(mixed);
  expect(f.calls.map((call) => call.url)).toEqual(["/api/v1/context/search"]);
});

it.each([false, true])(
  "shipped MCP bundle preserves both paths and restricts generic session: %s",
  async (generic) => {
    const f = generic
      ? await fixture(
          ["om_recall", "om_conversations"],
          ["all-apps", "raw-imported-all"],
          false,
          "generic-local-mcp",
        )
      : await fixture();
    f.respond(mixed);
    const env = {
      ...(await nativeEnvironment(f)),
      ONE_MEMORY_GENERIC_LOCAL_MCP: generic ? "1" : "0",
    };
    const result = await new Promise<string>((resolve, reject) => {
      const child = spawn(process.execPath, [path.resolve("dist/mcp.cjs")], {
        env,
        stdio: ["pipe", "pipe", "pipe"],
      });
      let stdout = "";
      let closedInput = false;
      const timer = setTimeout(() => {
        child.kill();
        reject(new Error("fixture MCP timeout"));
      }, 6000);
      child.stdout.on("data", (chunk) => {
        stdout += chunk;
        if (
          !closedInput &&
          stdout.split("\n").some((line) => {
            try {
              return JSON.parse(line).id === (generic ? 4 : 3);
            } catch {
              return false;
            }
          })
        ) {
          closedInput = true;
          child.stdin.end();
        }
      });
      child.on("error", (error) => {
        clearTimeout(timer);
        reject(error);
      });
      child.on("close", () => {
        clearTimeout(timer);
        resolve(stdout);
      });
      child.stdin.write(
        `${JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize" })}\n`,
      );
      child.stdin.write(
        `${JSON.stringify({ jsonrpc: "2.0", id: 2, method: "tools/list" })}\n`,
      );
      child.stdin.write(
        `${JSON.stringify({ jsonrpc: "2.0", id: 3, method: "tools/call", params: { name: "om_context", arguments: { query: "original" } } })}\n`,
      );
      if (generic)
        child.stdin.write(
          `${JSON.stringify({ jsonrpc: "2.0", id: 4, method: "tools/call", params: { name: "MemHub_conversations", arguments: { query: "original" } } })}\n`,
        );
    });
    const messages = result
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line));
    const names = messages
      .find((m) => m.id === 2)
      .result.tools.map((tool: { name: string }) => tool.name);
    if (generic) {
      expect(names).toEqual(["MemHub_recall", "MemHub_context"]);
      expect(messages.find((m) => m.id === 4)).toMatchObject({
        error: {
          code: -32602,
          message: "Tool not available for this connection",
        },
      });
    } else {
      expect(names).toContain("MemHub_context");
      expect(names).toContain("MemHub_conversations");
    }
    expect(messages.find((m) => m.id === 3).result.structuredContent).toEqual(
      mixed,
    );
  },
);

it.each([403, 404])(
  "shipped CLI reports %i without a legacy fallback",
  async (status) => {
    const f = await fixture();
    f.respond({ detail: "private" }, status);
    const result = await nativeCli(
      ["--context", "x", "--json"],
      await nativeEnvironment(f),
    );
    expect(result.code).toBe(status === 404 ? 5 : 4);
    expect(JSON.parse(result.stdout)).toMatchObject({
      state: "unavailable",
      reason: status === 404 ? "context_upgrade_required" : "host_scope_denied",
    });
    expect(f.calls.map((call) => call.url)).toEqual(["/api/v1/context/search"]);
  },
);

it("passes partial zero-hit coverage through instead of a successful empty", async () => {
  const f = await fixture();
  const partial = {
    ...empty,
    state: "partial",
    paths: {
      raw: lane,
      memory: {
        ...lane,
        status: "not_authorized",
        reason: "permission_required",
      },
    },
  };
  f.respond(partial);
  expect(
    await contextThroughBroker(
      f.discoveryPath,
      { query: "x" },
      { credentialProvider: () => f.issued.secret },
    ),
  ).toEqual(partial);
});

it("gives a lookup with no timeout of its own the host default, not Core's two seconds", async () => {
  const f = await fixture();
  await contextThroughBroker(
    f.discoveryPath,
    { query: "x" },
    { credentialProvider: () => f.issued.secret },
  );
  await contextThroughBroker(
    f.discoveryPath,
    { query: "x", timeout_ms: 500 },
    { credentialProvider: () => f.issued.secret },
  );
  expect(f.calls.map((call) => call.body)).toEqual([
    { query: "x", timeout_ms: HOST_CONTEXT_TIMEOUT_MS },
    { query: "x", timeout_ms: 500 },
  ]);
  expect(HOST_CONTEXT_TIMEOUT_MS).toBeGreaterThan(2000);
  expect(CONTEXT_INPUT_SCHEMA.properties.timeout_ms.default).toBe(
    HOST_CONTEXT_TIMEOUT_MS,
  );
});

it("gives a recall with no timeout of its own the same host default", async () => {
  const f = await fixture();
  await recallThroughBroker(
    f.discoveryPath,
    { query: "x" },
    { credentialProvider: () => f.issued.secret },
  ).catch(() => {});
  expect(f.calls).toHaveLength(1);
  expect(f.calls[0].body).toMatchObject({
    timeout_ms: HOST_CONTEXT_TIMEOUT_MS,
  });
});

it("reads quoted CLI runtime and credential paths exactly as installed", async () => {
  const { mkdir, writeFile } = await import("node:fs/promises");
  const root = await mkdtemp(path.join(os.tmpdir(), "om-context-skill-"));
  cleanups.push(() => rm(root, { recursive: true, force: true }));
  const host = piLayout(root);
  const runtime = {
    runtimeCommand: `${root}/node "$value"`,
    mcpEntryPath: `${root}/mcp.cjs`,
    recallCliPath: `${root}/read \\ file.cjs`,
    discoveryPath: `${root}/discovery "$name".json`,
    credentialFile: `${root}/credential "$private"`,
  };
  if (!host.skillPath) throw new Error("expected a CLI Skill path");
  await mkdir(path.dirname(host.skillPath), { recursive: true });
  await writeFile(host.skillPath, renderSkillContent("cli", runtime));
  const command = await readInstalledRecallCommand(host);
  expect(command?.command).toBe(runtime.runtimeCommand);
  expect(command?.args).toEqual([runtime.recallCliPath]);
  expect(command?.discoveryPath).toBe(runtime.discoveryPath);
  expect(command?.env.ONE_MEMORY_HOST_CREDENTIAL_FILE).toBe(
    runtime.credentialFile,
  );
});

function receiptFor(items: Array<typeof raw | typeof memory>) {
  return {
    ...mixed,
    items,
    used_characters: items.reduce(
      (total, item) =>
        total +
        ("messages" in item
          ? item.messages.reduce(
              (n, message) => n + [...message.text].length,
              0,
            )
          : [...item.content].length),
      0,
    ),
    paths: {
      raw: {
        ...lane,
        status: items.some((item) => item.kind === "raw") ? "ok" : "empty",
        returned_count: items.filter((item) => item.kind === "raw").length,
      },
      memory: {
        ...lane,
        status: items.some((item) => item.kind === "memory") ? "ok" : "empty",
        returned_count: items.filter((item) => item.kind === "memory").length,
      },
    },
  };
}

it.each([
  { tools: ["om_recall"], scopes: ["all-apps"], response: receiptFor([raw]) },
  {
    tools: ["om_recall"],
    scopes: [`workspace:v1:${"a".repeat(64)}`],
    response: receiptFor([raw]),
  },
  {
    tools: ["om_conversations"],
    scopes: ["all-apps", "source:codex"],
    response: receiptFor([memory]),
  },
  {
    tools: ["om_recall", "om_conversations"],
    scopes: ["all-apps", "source:codex"],
    response: receiptFor([{ ...raw, source_id: "cursor" }]),
  },
])(
  "checks captured identity on Core output: $tools $scopes",
  async ({ tools, scopes, response }) => {
    const f = await fixture(tools as HostReadTool[], scopes);
    f.respond(response);
    const result = await f.post();
    expect(result.status).toBe(502);
    expect(result.data).toEqual({ error: "context_unavailable" });
    expect(f.calls).toHaveLength(1);
  },
);

it.each([
  {
    tools: ["om_recall"],
    scopes: ["all-apps"],
    response: receiptFor([memory]),
  },
  {
    tools: ["om_recall"],
    scopes: [`workspace:v1:${"a".repeat(64)}`],
    response: receiptFor([memory]),
  },
  {
    tools: ["om_conversations"],
    scopes: ["all-apps", "source:codex"],
    response: receiptFor([raw]),
  },
])(
  "preserves correctly authorized single-lane output: $tools $scopes",
  async ({ tools, scopes, response }) => {
    const f = await fixture(tools as HostReadTool[], scopes);
    f.respond(response);
    const result = await f.post();
    expect(result.status).toBe(200);
    expect(result.data).toEqual(response);
  },
);

it.each([
  { ...empty, state: "grounded" },
  { ...mixed, state: "empty" },
  { ...mixed, state: "unavailable" },
  receiptFor([{ ...raw, anchor_evidence_id: "absent" }]),
  receiptFor([{ ...raw, messages: [raw.messages[0], raw.messages[0]] }]),
])("rejects contradictory or untraceable Core payload %#", async (response) => {
  const f = await fixture();
  f.respond(response);
  const result = await f.post();
  expect(result.status).toBe(502);
  expect(result.data).toEqual({ error: "context_unavailable" });
});

it("limits the workspace CLI instruction to the legacy command", () => {
  const text = renderSkillContent("cli", {
    runtimeCommand: "/fixture/node",
    mcpEntryPath: "/fixture/mcp.cjs",
    recallCliPath: "/fixture/recall.cjs",
    discoveryPath: "/fixture/discovery.json",
  });
  expect(text).toContain(
    'Only for the legacy command, keep `--workspace-path "$PWD"`',
  );
  expect(text).not.toContain('- Keep `--workspace-path "$PWD"` as written.');
});
