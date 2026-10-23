import { mkdtemp, readFile } from "node:fs/promises";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import os from "node:os";
import path from "node:path";
import axios from "axios";
import { afterEach, describe, expect, it } from "vitest";

import { readDiscovery } from "./discovery.js";
import { expectRestrictedFileMode } from "./expect-restricted-mode.js";
import { HostGrantStore } from "./host-grants.js";
import { RecallBroker } from "./recall-broker.js";

const closers: Array<() => Promise<void>> = [];

afterEach(async () => {
  await Promise.allSettled(closers.splice(0).map((close) => close()));
});

describe("RecallBroker", () => {
  it("exchanges a one-time custom Harness code for an existing host credential", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "om-broker-pairing-"));
    const store = await HostGrantStore.initialize(path.join(root, "grants"));
    const pairing = await store.createPairing({ displayName: "Kimi Code" });
    const broker = new RecallBroker({
      coreBaseUrl: "http://127.0.0.1:1",
      coreApiToken: "pairing-control-secret",
      discoveryPath: path.join(root, "broker.json"),
      instanceId: "pairing-instance",
      preferredPort: 0,
      hostGrantStore: store,
    });
    await broker.start();
    closers.push(() => broker.stop());
    const first = await axios.post(
      `http://127.0.0.1:${broker.port}/pair`,
      { code: pairing.pairingCode },
      { headers: { "Content-Type": "application/json" } },
    );
    expect(first.data).toMatchObject({
      schema_version: 1,
      host_id: pairing.hostId,
      credential_type: "bearer",
      credential: expect.stringMatching(/^omhg_v1_[A-Za-z0-9_-]{43}$/),
    });
    expect(await store.authenticate(first.data.credential)).toMatchObject({
      hostId: pairing.hostId,
      status: "approved",
    });
    const second = await axios.post(
      `http://127.0.0.1:${broker.port}/pair`,
      { code: pairing.pairingCode },
      {
        headers: { "Content-Type": "application/json" },
        validateStatus: () => true,
      },
    );
    expect(second.status).toBe(401);
    expect(second.data).toEqual({ error: "host_pairing_invalid" });
  });

  it("does not log successful connection polling", async () => {
    const diagnostics: Array<Record<string, unknown>> = [];
    const root = await mkdtemp(path.join(os.tmpdir(), "om-broker-poll-log-"));
    let available = true;
    let now = 0;
    const broker = new RecallBroker({
      coreBaseUrl: "http://127.0.0.1:1",
      coreApiToken: "poll-secret",
      discoveryPath: path.join(root, "broker.json"),
      instanceId: "poll-instance",
      preferredPort: 0,
      connectionControl: async () => ({ available }),
      diagnostics: async (event) => void diagnostics.push(event),
      now: () => now,
    });
    await broker.start();
    closers.push(() => broker.stop());
    const poll = () =>
      axios.post(
        `http://127.0.0.1:${broker.port}/control/connections`,
        {
          schema_version: 1,
          instance_id: "poll-instance",
          action: "host",
          input: {},
        },
        {
          headers: {
            Authorization: "Bearer poll-secret",
            "Content-Type": "application/json",
          },
        },
      );
    for (let index = 0; index < 100; index += 1) await poll();
    now = 5 * 60 * 1000;
    await poll();
    available = false;
    await poll();
    expect(diagnostics).toEqual([]);
  });

  it("logs one repeated connection failure plus a five-minute summary", async () => {
    const diagnostics: Array<Record<string, unknown>> = [];
    const root = await mkdtemp(path.join(os.tmpdir(), "om-broker-poll-fail-"));
    let now = 0;
    const broker = new RecallBroker({
      coreBaseUrl: "http://127.0.0.1:1",
      coreApiToken: "poll-secret",
      discoveryPath: path.join(root, "broker.json"),
      instanceId: "poll-instance",
      preferredPort: 0,
      connectionControl: async () => {
        throw Error("host_install_failed");
      },
      diagnostics: async (event) => void diagnostics.push(event),
      now: () => now,
    });
    await broker.start();
    closers.push(() => broker.stop());
    const poll = () =>
      axios.post(
        `http://127.0.0.1:${broker.port}/control/connections`,
        {
          schema_version: 1,
          instance_id: "poll-instance",
          action: "host",
          input: {},
        },
        {
          headers: {
            Authorization: "Bearer poll-secret",
            "Content-Type": "application/json",
          },
          validateStatus: () => true,
        },
      );
    for (let index = 0; index < 100; index += 1) await poll();
    now = 5 * 60 * 1000;
    await poll();
    expect(diagnostics.map((event) => event.event)).toEqual([
      "connection_control.failed",
      "connection_control.summary",
    ]);
    expect(diagnostics[1]).toMatchObject({
      details: { action: "host", count: 101, error_count: 101 },
    });
  });

  it("emits an exception diagnostic for a whitelist-out-of-range error", async () => {
    const diagnostics: Array<Record<string, unknown>> = [];
    const root = await mkdtemp(path.join(os.tmpdir(), "om-broker-except-"));
    const broker = new RecallBroker({
      coreBaseUrl: "http://127.0.0.1:1",
      coreApiToken: "except-secret",
      discoveryPath: path.join(root, "broker.json"),
      instanceId: "except-instance",
      preferredPort: 0,
      connectionControl: async () => {
        throw Error("unexpected_runtime_crash");
      },
      diagnostics: async (event) => void diagnostics.push(event),
    });
    await broker.start();
    closers.push(() => broker.stop());
    const response = await axios.post(
      `http://127.0.0.1:${broker.port}/control/connections`,
      {
        schema_version: 1,
        instance_id: "except-instance",
        action: "host",
        input: {},
      },
      {
        headers: {
          Authorization: "Bearer except-secret",
          "Content-Type": "application/json",
        },
        validateStatus: () => true,
      },
    );
    expect(response.status).toBe(503);
    expect(
      diagnostics.some(
        (event) => event.event === "connection_control.exception",
      ),
    ).toBe(true);
    const exception = diagnostics.find(
      (event) => event.event === "connection_control.exception",
    );
    expect(exception).toMatchObject({
      level: "error",
      component: "host.broker",
      outcome: "failed",
      error_code: "connection_control_unavailable",
      error_type: "Error",
      details: { action: "host" },
    });
    expect(exception?.details).not.toHaveProperty("message");
    expect(exception?.details).not.toHaveProperty("stack_origin");
  });
  it.each(["recall", "ask"])(
    "answers %s without waiting for a slow diagnostic write",
    async (endpoint) => {
      const events: string[] = [];
      const core = createServer((request, response) => {
        response.setHeader("Content-Type", "application/json");
        response.end(
          JSON.stringify(
            request.url === "/api/v2/memory-authority"
              ? { authority: "legacy", migration_id: null }
              : request.url === "/api/v1/memories/ask"
                ? { answer: "empty", memories: [], mode: "empty" }
                : { state: "empty" },
          ),
        );
      });
      const corePort = await listen(core, 0);
      closers.push(() => close(core));
      const root = await mkdtemp(path.join(os.tmpdir(), "om-slow-log-"));
      const broker = new RecallBroker({
        coreBaseUrl: `http://127.0.0.1:${corePort}`,
        coreApiToken: "test-slow-log",
        discoveryPath: path.join(root, "broker.json"),
        instanceId: "slow-log-instance",
        preferredPort: 0,
        diagnostics: (event) => {
          events.push(event.event);
          return new Promise(() => undefined);
        },
      });
      await broker.start();
      closers.push(() => broker.stop());
      const response = await axios.post(
        `http://127.0.0.1:${broker.port}/${endpoint}`,
        { [endpoint === "recall" ? "query" : "question"]: "project" },
        {
          headers: {
            "Content-Type": "application/json",
            "X-One-Memory-Client": "cursor-mcp",
          },
          timeout: 2000,
          validateStatus: () => true,
        },
      );
      expect(response.status).toBe(200);
      expect(events[0]).toBe(`${endpoint}.started`);
    },
  );
  it.each(["recall", "ask"])(
    "answers %s when the diagnostic writer throws synchronously",
    async (endpoint) => {
      const rejections: unknown[] = [];
      const onRejection = (reason: unknown) => void rejections.push(reason);
      process.on("unhandledRejection", onRejection);
      closers.push(async () => {
        process.off("unhandledRejection", onRejection);
      });
      const core = createServer((request, response) => {
        response.setHeader("Content-Type", "application/json");
        response.end(
          JSON.stringify(
            request.url === "/api/v2/memory-authority"
              ? { authority: "legacy", migration_id: null }
              : request.url === "/api/v1/memories/ask"
                ? { answer: "empty", memories: [], mode: "empty" }
                : { state: "empty" },
          ),
        );
      });
      const corePort = await listen(core, 0);
      closers.push(() => close(core));
      const root = await mkdtemp(path.join(os.tmpdir(), "om-throwing-log-"));
      const broker = new RecallBroker({
        coreBaseUrl: `http://127.0.0.1:${corePort}`,
        coreApiToken: "test-throwing-log",
        discoveryPath: path.join(root, "broker.json"),
        instanceId: "throwing-log-instance",
        preferredPort: 0,
        diagnostics: () => {
          throw new Error("diagnostic writer unavailable");
        },
      });
      await broker.start();
      closers.push(() => broker.stop());
      const response = await axios.post(
        `http://127.0.0.1:${broker.port}/${endpoint}`,
        { [endpoint === "recall" ? "query" : "question"]: "project" },
        {
          headers: {
            "Content-Type": "application/json",
            "X-One-Memory-Client": "cursor-mcp",
          },
          timeout: 2000,
          validateStatus: () => true,
        },
      );
      await new Promise((resolve) => setTimeout(resolve, 50));
      expect(response.status).toBe(200);
      expect(rejections).toEqual([]);
    },
  );
  it.each(["recall", "ask"])(
    "preserves %s scope denial instead of claiming Core is offline",
    async (endpoint) => {
      const diagnostics: Array<Record<string, unknown>> = [];
      const core = createServer((request, response) => {
        if (request.url === "/api/v2/memory-authority") {
          response.setHeader("Content-Type", "application/json");
          response.end(
            JSON.stringify({ authority: "legacy", migration_id: null }),
          );
          return;
        }
        response.statusCode = 403;
        response.setHeader("Content-Type", "application/json");
        response.end(
          JSON.stringify({ detail: "external_recall_scope_denied" }),
        );
      });
      const corePort = await listen(core, 0);
      closers.push(() => close(core));
      const root = await mkdtemp(path.join(os.tmpdir(), "om-denied-scope-"));
      const broker = new RecallBroker({
        coreBaseUrl: `http://127.0.0.1:${corePort}`,
        coreApiToken: "test-scope",
        discoveryPath: path.join(root, "broker.json"),
        instanceId: "scope-instance",
        preferredPort: 0,
        diagnostics: async (event) => void diagnostics.push(event),
      });
      await broker.start();
      closers.push(() => broker.stop());
      const response = await axios.post(
        `http://127.0.0.1:${broker.port}/${endpoint}`,
        {
          [endpoint === "recall" ? "query" : "question"]: "project",
          scope: "*",
        },
        {
          headers: {
            "Content-Type": "application/json",
            "X-One-Memory-Client": "cursor-mcp",
          },
          validateStatus: () => true,
        },
      );
      expect(response.status).toBe(403);
      expect(response.data.error).toBe("external_recall_scope_denied");
      expect(diagnostics.at(-1)).toMatchObject({
        event: `${endpoint}.completed`,
        outcome: "failed",
        error_code: "broker_http_403",
      });
      expect(JSON.stringify(diagnostics)).not.toContain(
        "external_recall_scope_denied",
      );
    },
  );
  it("proxies only recall and keeps the Core bearer token out of discovery", async () => {
    let authorization: string | undefined;
    let readContext: string | string[] | undefined;
    let traceId: string | string[] | undefined;
    const diagnostics: Array<Record<string, unknown>> = [];
    const core = createServer((request, response) => {
      authorization = request.headers.authorization;
      readContext = request.headers["x-one-memory-read-context"];
      traceId = request.headers["x-one-memory-trace-id"];
      response.setHeader("Content-Type", "application/json");
      response.end(
        JSON.stringify({
          state: "grounded",
          query: "产品经理",
          scope: "all-apps",
          hits: [],
          used_characters: 0,
          truncated: false,
        }),
      );
    });
    const corePort = await listen(core, 0);
    closers.push(() => close(core));
    const root = await mkdtemp(path.join(os.tmpdir(), "om-broker-"));
    const discoveryPath = path.join(root, "cursor-broker.json");
    const broker = new RecallBroker({
      coreBaseUrl: `http://127.0.0.1:${corePort}`,
      coreApiToken: "master-secret",
      discoveryPath,
      instanceId: "owned-instance",
      preferredPort: 0,
      diagnostics: async (event) => void diagnostics.push(event),
    });
    await broker.start();
    closers.push(() => broker.stop());

    const response = await axios.post(
      `http://127.0.0.1:${broker.port}/recall`,
      { query: "产品经理" },
      {
        headers: {
          "Content-Type": "application/json",
          "X-One-Memory-Client": "cursor-mcp",
          "X-One-Memory-Trace-Id": "11111111-1111-4111-8111-111111111111",
        },
      },
    );

    expect(response.data.state).toBe("grounded");
    expect(authorization).toBe("Bearer master-secret");
    expect(readContext).toBe("external");
    expect(traceId).toBe("11111111-1111-4111-8111-111111111111");
    expect(response.headers["x-one-memory-trace-id"]).toBe(
      "11111111-1111-4111-8111-111111111111",
    );
    expect(diagnostics.map((event) => event.event)).toEqual([
      "recall.started",
      "recall.completed",
    ]);
    expect(diagnostics).toEqual([
      expect.objectContaining({ details: { source_id: "cursor" } }),
      expect.objectContaining({
        details: { source_id: "cursor", status: "200" },
      }),
    ]);
    expect(await readFile(discoveryPath, "utf8")).not.toContain(
      "master-secret",
    );
    await expectRestrictedFileMode(discoveryPath);
    await expect(
      axios.get(`http://127.0.0.1:${broker.port}/api/v1/memories`),
    ).rejects.toMatchObject({ response: { status: 404 } });
  });

  it("falls back safely when the preferred port is occupied and publishes it", async () => {
    const occupied = createServer();
    const occupiedPort = await listen(occupied, 0);
    closers.push(() => close(occupied));
    const root = await mkdtemp(path.join(os.tmpdir(), "om-broker-port-"));
    const discoveryPath = path.join(root, "cursor-broker.json");
    const broker = new RecallBroker({
      coreBaseUrl: "http://127.0.0.1:1",
      coreApiToken: "memory-only",
      discoveryPath,
      instanceId: "fallback-instance",
      preferredPort: occupiedPort,
    });
    const discovery = await broker.start();
    closers.push(() => broker.stop());

    expect(discovery.port).not.toBe(occupiedPort);
    expect((await readDiscovery(discoveryPath)).port).toBe(discovery.port);

    await broker.stop();
    closers.pop();
    await expect(readDiscovery(discoveryPath)).resolves.toMatchObject({
      status: "stopped",
      port: null,
    });
  });

  // A post-start broker fault must be observable by the owning
  // Runtime and must NOT become an unhandled rejection that terminates the
  // process. Before this, the request handler was fired with
  // `void this.handle(...)` and no catch, so a handler rejection killed the
  // daemon that hosts the broker.
  it("captures a rejected request handler as a fault instead of crashing", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "om-broker-fault-"));
    const faults: string[] = [];
    const unhandled: unknown[] = [];
    const onUnhandled = (reason: unknown) => void unhandled.push(reason);
    process.on("unhandledRejection", onUnhandled);
    // Force the handler itself to reject. The guard wraps `this.handle(...)`
    // at call time, so patching the instance method is exactly the production
    // failure mode: a handler rejection that would otherwise be unhandled.
    const broker = new RecallBroker({
      coreBaseUrl: "http://127.0.0.1:1",
      coreApiToken: "fault-secret",
      discoveryPath: path.join(root, "broker.json"),
      instanceId: "fault-instance",
      preferredPort: 0,
      onFault: (code) => void faults.push(code),
    });
    try {
      await broker.start();
      closers.push(() => broker.stop());
      (broker as unknown as { handle: () => Promise<void> }).handle =
        async () => {
          throw new Error("forced handler failure");
        };
      const response = await axios.get(
        `http://127.0.0.1:${broker.port}/health`,
        { validateStatus: () => true },
      );
      await new Promise((resolve) => setTimeout(resolve, 50));
      expect(unhandled).toEqual([]);
      expect(faults).toContain("broker_request_handler_failed");
      expect(response.status).toBe(500);
      // Restore the real handler: the broker must still serve afterwards,
      // which is the "sidecar fault is contained" property.
      delete (broker as unknown as { handle?: unknown }).handle;
      await expect(
        axios.get(`http://127.0.0.1:${broker.port}/health`),
      ).resolves.toMatchObject({ data: { status: "ok" } });
    } finally {
      process.off("unhandledRejection", onUnhandled);
    }
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
