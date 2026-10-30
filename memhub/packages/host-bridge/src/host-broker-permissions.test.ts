import { spawn } from "node:child_process";
import { once } from "node:events";
import {
  chmod,
  mkdtemp,
  readFile,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import {
  createServer,
  type IncomingHttpHeaders,
  type ServerResponse,
} from "node:http";
import type { AddressInfo } from "node:net";
import os from "node:os";
import path from "node:path";
import { createInterface } from "node:readline";
import { inspect } from "node:util";
import axios from "axios";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  type BrokerClientOptions,
  readHostCredentialFile,
  recallThroughBroker,
} from "./broker-client.js";
import { HostGrantStore, type HostReadTool } from "./host-grants.js";
import { RecallBroker } from "./recall-broker.js";

const roots: string[] = [];
const closers: Array<() => Promise<void>> = [];
const workspace = `workspace:v1:${"a".repeat(64)}`;
const otherWorkspace = `workspace:v1:${"b".repeat(64)}`;
const coreToken = "isolated-core-only-token";
// MemHub_ask and MemHub_conversations are gone; recall is the one
// grant-governed read route left on this transport besides /context.
const endpoints = ["recall"] as const;
type Endpoint = (typeof endpoints)[number];
const tools: Record<Endpoint, HostReadTool> = {
  recall: "om_recall",
};
const paths: Record<Endpoint, string> = {
  recall: "/api/v1/recall",
};
interface CoreCall {
  url: string | undefined;
  headers: IncomingHttpHeaders;
  body: unknown;
}

afterEach(async () => {
  vi.unstubAllEnvs();
  for (const close of closers.splice(0).reverse()) await close();
  await Promise.all(
    roots.splice(0).map((root) => rm(root, { recursive: true, force: true })),
  );
});

function body(_endpoint: Endpoint): Record<string, unknown> {
  return { query: "fixture" };
}

function deferred() {
  let resolve!: () => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<void>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function corePayload(_url: string | undefined): unknown {
  return {
    state: "grounded",
    query: "fixture",
    scope: "all-apps",
    hits: [],
    used_characters: 0,
    truncated: false,
  };
}

function markedPayload(_url: string | undefined, marker: string): unknown {
  return { content: marker };
}

async function fixture(
  options: {
    legacy?: boolean;
    allowedTools?: HostReadTool[];
    allowedScopes?: string[];
    onCore?: (call: CoreCall, response: ServerResponse) => Promise<void>;
  } = {},
) {
  const root = await mkdtemp(path.join(os.tmpdir(), "om-host-broker-"));
  roots.push(root);
  const directory = path.join(root, "grants");
  const store = await HostGrantStore.initialize(directory);
  const issued = await store.createInstallation({ hostId: "cursor" });
  const receipt = await store.approveGrants({
    installationId: issued.receipt.installationId,
    expectedRevision: issued.receipt.revision,
    allowedTools: options.allowedTools ?? Object.values(tools),
    allowedScopes: options.allowedScopes ?? ["all-apps", workspace],
  });
  const credentialFile = path.join(root, "host-credential");
  await writeFile(credentialFile, `${issued.secret}\n`, { mode: 0o600 });
  const calls: CoreCall[] = [];
  const core = createServer((request, response) => {
    void (async () => {
      const chunks: Buffer[] = [];
      for await (const chunk of request) chunks.push(Buffer.from(chunk));
      const raw = Buffer.concat(chunks).toString("utf8");
      const call = {
        url: request.url,
        headers: request.headers,
        body: raw ? JSON.parse(raw) : undefined,
      };
      calls.push(call);
      if (options.onCore) return options.onCore(call, response);
      response.setHeader("Content-Type", "application/json");
      response.end(JSON.stringify(corePayload(request.url)));
    })().catch(() => {
      response.statusCode = 500;
      response.end("fixture failure");
    });
  });
  await new Promise<void>((resolve) => core.listen(0, "127.0.0.1", resolve));
  closers.push(async () => {
    core.closeAllConnections();
    await new Promise<void>((resolve, reject) =>
      core.close((error) => (error ? reject(error) : resolve())),
    );
  });
  const discoveryPath = path.join(root, "broker.json");
  const broker = new RecallBroker({
    coreBaseUrl: `http://127.0.0.1:${(core.address() as AddressInfo).port}`,
    coreApiToken: coreToken,
    discoveryPath,
    instanceId: "isolated-host-broker",
    preferredPort: 0,
    ...(options.legacy ? {} : { hostGrantStore: store }),
  });
  await broker.start();
  closers.push(() => broker.stop());
  const baseUrl = `http://127.0.0.1:${broker.port}`;
  const post = (
    endpoint: Endpoint,
    token?: string,
    input = body(endpoint),
    extraHeaders: Record<string, string> = {},
  ) =>
    axios.post(`${baseUrl}/${endpoint}`, input, {
      headers: {
        "Content-Type": "application/json",
        "X-One-Memory-Client": "cursor-mcp",
        ...(token === undefined ? {} : { Authorization: `Bearer ${token}` }),
        ...extraHeaders,
      },
      proxy: false,
      maxRedirects: 0,
      timeout: 5_000,
      validateStatus: () => true,
    });
  return {
    root,
    directory,
    store,
    issued,
    receipt,
    credentialFile,
    discoveryPath,
    baseUrl,
    calls,
    post,
  };
}

function clientCall(
  endpoint: Endpoint,
  discoveryPath: string,
  options?: BrokerClientOptions,
) {
  switch (endpoint) {
    case "recall":
      return recallThroughBroker(discoveryPath, { query: "fixture" }, options);
  }
}

describe.skipIf(process.platform === "win32")(
  "host capability broker transport",
  () => {
    it.each(endpoints)(
      "forwards verified grants, not caller headers, for %s",
      async (endpoint) => {
        const context = await fixture();
        const response = await context.post(
          endpoint,
          context.issued.secret,
          body(endpoint),
          {
            "X-One-Memory-Client": "not-an-identity",
            "x-one-memory-installation-id": "forged-installation",
            "x-one-memory-host-id": "forged-host",
            "x-one-memory-installation-revision": "999",
            "x-one-memory-allowed-scopes": '["*"]',
            "x-one-memory-allowed-tools": '["admin"]',
            "x-one-memory-read-context": "internal",
          },
        );
        expect(response.status).toBe(200);
        expect(response.data).toEqual(corePayload(paths[endpoint]));
        expect(context.calls).toHaveLength(1);
        expect(context.calls[0].url).toBe(paths[endpoint]);
        expect(context.calls[0].headers).toMatchObject({
          authorization: `Bearer ${coreToken}`,
          "x-one-memory-installation-id": context.receipt.installationId,
          "x-one-memory-host-id": context.receipt.hostId,
          "x-one-memory-installation-revision": String(
            context.receipt.revision,
          ),
          "x-one-memory-allowed-scopes": JSON.stringify(
            context.receipt.allowedScopes,
          ),
          "x-one-memory-allowed-tools": JSON.stringify(
            context.receipt.allowedTools,
          ),
          "x-one-memory-read-context": "external",
        });
        for (const value of [
          context.calls,
          response.data,
          await readFile(context.discoveryPath, "utf8"),
        ]) {
          expect(JSON.stringify(value)).not.toContain(context.issued.secret);
          expect(JSON.stringify(value)).not.toContain(context.credentialFile);
        }
        expect(response.headers["cache-control"]).toBe("no-store");
      },
    );

    it.each(endpoints)(
      "rejects missing, forged and install-only credentials for %s before Core",
      async (endpoint) => {
        const context = await fixture();
        const pending = await context.store.createInstallation({
          hostId: "cursor",
        });
        for (const token of [
          undefined,
          "cursor-mcp",
          context.receipt.installationId,
          `omhg_v1_${"a".repeat(43)}`,
          pending.secret,
        ]) {
          const result = await context.post(endpoint, token, body(endpoint), {
            "x-one-memory-installation-id": context.receipt.installationId,
            "x-one-memory-allowed-tools": JSON.stringify(Object.values(tools)),
            "x-one-memory-allowed-scopes": '["all-apps"]',
          });
          expect([401, 403]).toContain(result.status);
          expect(JSON.stringify(result.data)).not.toContain(pending.secret);
        }
        expect(context.calls).toHaveLength(0);
        expect(
          (await context.post(endpoint, context.issued.secret)).status,
        ).toBe(200);
        expect(context.calls).toHaveLength(1);
      },
    );

    it.each(endpoints)(
      "checks the specific %s tool grant",
      async (endpoint) => {
        const allowedTool = endpoint === "recall" ? "om_ask" : "om_recall";
        const context = await fixture({ allowedTools: [allowedTool] });
        const result = await context.post(endpoint, context.issued.secret);
        expect(result.status).toBe(403);
        expect(result.data.error).toBe("host_grant_tool_denied");
        expect(context.calls).toHaveLength(0);
      },
    );

    it("allows diagnostic trace only with its explicit read-only grant", async () => {
      const granted = await fixture({
        allowedTools: ["om_recall", "om_diagnostic_trace"],
        allowedScopes: ["all-apps"],
        onCore: async (_call, response) => {
          response.setHeader("Content-Type", "application/json");
          response.end(
            JSON.stringify({
              schema_version: 1,
              state: "disabled",
              events: [],
            }),
          );
        },
      });
      const response = await axios.post(
        `${granted.baseUrl}/diagnostics/trace`,
        { trace_id: "nonexistent-qa-trace" },
        {
          headers: { Authorization: `Bearer ${granted.issued.secret}` },
          proxy: false,
          validateStatus: () => true,
        },
      );
      expect(response.status).toBe(200);
      expect(response.data).toEqual({
        schema_version: 1,
        state: "disabled",
        events: [],
      });

      const denied = await fixture({
        allowedTools: ["om_recall"],
        allowedScopes: ["all-apps"],
      });
      const refused = await axios.post(
        `${denied.baseUrl}/diagnostics/trace`,
        { trace_id: "nonexistent-qa-trace" },
        {
          headers: { Authorization: `Bearer ${denied.issued.secret}` },
          proxy: false,
          validateStatus: () => true,
        },
      );
      expect(refused.status).toBe(403);
      expect(denied.calls).toHaveLength(0);
    });

    it("uses the latest grant on every request without restarting the broker", async () => {
      const context = await fixture({
        allowedTools: ["om_recall"],
        allowedScopes: ["all-apps"],
      });
      expect((await context.post("recall", context.issued.secret)).status).toBe(
        200,
      );

      const recallOff = await context.store.approveGrants({
        installationId: context.receipt.installationId,
        expectedRevision: context.receipt.revision,
        allowedTools: ["om_ask"],
        allowedScopes: ["all-apps"],
      });
      const denied = await context.post("recall", context.issued.secret);
      expect(denied.status).toBe(403);
      expect(denied.data.error).toBe("host_grant_tool_denied");
      expect(context.calls).toHaveLength(1);

      await context.store.approveGrants({
        installationId: context.receipt.installationId,
        expectedRevision: recallOff.revision,
        allowedTools: ["om_recall"],
        allowedScopes: ["all-apps"],
      });
      expect((await context.post("recall", context.issued.secret)).status).toBe(
        200,
      );
      expect(context.calls).toHaveLength(2);
    });

    it.each(endpoints)(
      "denies invalid %s scopes and forwards only approved grants for workspace expansion",
      async (endpoint) => {
        const context = await fixture({ allowedScopes: [workspace] });
        for (const scope of [
          undefined,
          "",
          "*",
          "private",
          "workspace:*",
          otherWorkspace,
        ]) {
          const result = await context.post(endpoint, context.issued.secret, {
            ...body(endpoint),
            scope,
          });
          expect([400, 403]).toContain(result.status);
        }
        expect(context.calls).toHaveLength(0);
        const input = {
          ...body(endpoint),
          scope: workspace,
          workspace_path: path.join(context.root, "untrusted-workspace-hint"),
        };
        expect(
          (await context.post(endpoint, context.issued.secret, input)).status,
        ).toBe(200);
        expect(context.calls[0].body).toEqual(input);
        expect(context.calls[0].headers["x-one-memory-allowed-scopes"]).toBe(
          JSON.stringify([workspace]),
        );
      },
    );

    it.each([
      ["POST", "/ask"],
      ["POST", "/conversations"],
      ["POST", "/memory-writes"],
      ["GET", "/memory-writes/req-1"],
    ] as const)(
      "no longer serves the removed %s %s route",
      async (method, route) => {
        const context = await fixture();
        const response = await axios.request({
          method,
          url: `${context.baseUrl}${route}`,
          data: method === "POST" ? { query: "fixture" } : undefined,
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${context.issued.secret}`,
          },
          proxy: false,
          validateStatus: () => true,
        });
        expect(response.status).toBe(404);
        expect(context.calls).toHaveLength(0);
      },
    );

    it.each(endpoints)(
      "suppresses in-flight %s content after revocation",
      async (endpoint) => {
        const entered = deferred();
        const release = deferred();
        const context = await fixture({
          onCore: async (call, response) => {
            entered.resolve();
            await release.promise;
            response.setHeader("Content-Type", "application/json");
            response.end(
              JSON.stringify(
                markedPayload(call.url, "must-not-release-old-content"),
              ),
            );
          },
        });
        const pending = context.post(endpoint, context.issued.secret);
        try {
          await entered.promise;
          await context.store.revoke({
            installationId: context.receipt.installationId,
            expectedRevision: context.receipt.revision,
          });
        } finally {
          release.resolve();
        }
        const response = await pending;
        expect([401, 403]).toContain(response.status);
        expect(JSON.stringify(response.data)).not.toContain(
          "must-not-release-old-content",
        );
        expect(context.calls).toHaveLength(1);
        expect([401, 403]).toContain(
          (await context.post(endpoint, context.issued.secret)).status,
        );
        expect(context.calls).toHaveLength(1);
      },
    );

    it.each(["update", "rotate", "corrupt", "unrelated"] as const)(
      "rechecks installation revision at response release: %s",
      async (mutation) => {
        const entered = deferred();
        const release = deferred();
        const context = await fixture({
          onCore: async (call, response) => {
            entered.resolve();
            await release.promise;
            response.end(JSON.stringify(markedPayload(call.url, "old-result")));
          },
        });
        const pending = context.post("recall", context.issued.secret);
        try {
          await entered.promise;
          const input = {
            installationId: context.receipt.installationId,
            expectedRevision: context.receipt.revision,
          };
          if (mutation === "update")
            await context.store.approveGrants({
              ...input,
              allowedTools: ["om_ask"],
              allowedScopes: ["all-apps"],
            });
          if (mutation === "rotate") await context.store.rotateSecret(input);
          if (mutation === "corrupt")
            await writeFile(
              path.join(context.directory, "host-grants.json"),
              "corrupt",
            );
          if (mutation === "unrelated")
            await context.store.createInstallation({ hostId: "codex" });
        } finally {
          release.resolve();
        }
        const response = await pending;
        expect(response.status === 200).toBe(mutation === "unrelated");
        if (mutation !== "unrelated")
          expect(JSON.stringify(response.data)).not.toContain("old-result");
      },
    );

    it.each(endpoints)(
      "preserves Core authorization failure without echoing its error body for %s",
      async (endpoint) => {
        const context = await fixture({
          onCore: async (_call, response) => {
            response.statusCode = 403;
            response.end(
              JSON.stringify({ detail: "sensitive Core diagnostic" }),
            );
          },
        });
        const response = await context.post(endpoint, context.issued.secret);
        expect(response.status).toBe(403);
        expect(JSON.stringify(response.data)).not.toContain(
          "sensitive Core diagnostic",
        );
      },
    );

    it("fails closed on corrupt grants and keeps legacy mode explicit", async () => {
      const context = await fixture();
      await writeFile(
        path.join(context.directory, "host-grants.json"),
        `broken ${context.issued.secret}`,
      );
      const response = await context.post("recall", context.issued.secret);
      expect(response.status).toBe(503);
      expect(context.calls).toHaveLength(0);
      expect(JSON.stringify(response.data)).not.toContain(
        context.issued.secret,
      );
      expect(JSON.stringify(response.data)).not.toContain(context.root);
      const legacy = await fixture({ legacy: true });
      expect((await legacy.post("recall")).status).toBe(200);
      expect(legacy.calls[0].headers).not.toHaveProperty(
        "x-one-memory-installation-id",
      );
    });

    it.each(endpoints)(
      "never falls back to legacy when the %s grant store disappears",
      async (endpoint) => {
        const context = await fixture();
        expect(
          (await context.post(endpoint, context.issued.secret)).status,
        ).toBe(200);
        await rm(path.join(context.directory, "host-grants.json"));
        const response = await context.post(endpoint, context.issued.secret);
        expect(response.status).toBe(503);
        expect(context.calls).toHaveLength(1);
        expect(JSON.stringify(response.data)).not.toContain(context.root);
        expect(JSON.stringify(response.data)).not.toContain(
          context.issued.secret,
        );
      },
    );

    it("does not follow Core redirects with internal credentials or grants", async () => {
      const received: IncomingHttpHeaders[] = [];
      const receiver = createServer((request, response) => {
        received.push(request.headers);
        response.end("redirect was followed");
      });
      await new Promise<void>((resolve) =>
        receiver.listen(0, "127.0.0.1", resolve),
      );
      closers.push(
        () => new Promise<void>((resolve) => receiver.close(() => resolve())),
      );
      const destination = `http://127.0.0.1:${(receiver.address() as AddressInfo).port}/redirect`;
      await axios.get(destination, { proxy: false });
      expect(received).toHaveLength(1);
      const context = await fixture({
        onCore: async (_call, response) => {
          response.statusCode = 307;
          response.setHeader("Location", destination);
          response.end("sensitive redirect diagnostic");
        },
      });
      for (const endpoint of endpoints) {
        const response = await context.post(endpoint, context.issued.secret);
        expect(response.status).toBe(502);
        expect(JSON.stringify(response.data)).not.toContain(
          "sensitive redirect diagnostic",
        );
      }
      expect(context.calls).toHaveLength(endpoints.length);
      expect(received).toHaveLength(1);
    });

    it("does not accept identity or secret tool arguments", async () => {
      const context = await fixture();
      for (const endpoint of endpoints) {
        for (const claimed of [
          { host_id: "cursor" },
          { secret: context.issued.secret },
          { installation_id: context.receipt.installationId },
        ]) {
          expect(
            (
              await context.post(endpoint, context.issued.secret, {
                ...body(endpoint),
                ...claimed,
              })
            ).status,
          ).toBe(400);
        }
      }
      expect(context.calls).toHaveLength(0);
    });
  },
);

describe.skipIf(process.platform === "win32")(
  "private host credential client",
  () => {
    it.each(endpoints)(
      "injects a credential provider into %s without exposing its secret",
      async (endpoint) => {
        const context = await fixture();
        const provider = vi.fn(() =>
          readHostCredentialFile(context.credentialFile),
        );
        await expect(
          clientCall(endpoint, context.discoveryPath, {
            credentialProvider: provider,
          }),
        ).resolves.toEqual(corePayload(paths[endpoint]));
        expect(provider).toHaveBeenCalledTimes(1);
        expect(context.calls[0].headers["x-one-memory-installation-id"]).toBe(
          context.receipt.installationId,
        );
        expect(JSON.stringify(context.calls)).not.toContain(
          context.issued.secret,
        );
      },
    );

    it("reads a bounded raw token file again after rotation", async () => {
      const context = await fixture();
      expect(await readHostCredentialFile(context.credentialFile)).toBe(
        context.issued.secret,
      );
      const rotated = await context.store.rotateSecret({
        installationId: context.receipt.installationId,
        expectedRevision: context.receipt.revision,
      });
      await writeFile(context.credentialFile, rotated.secret);
      expect(await readHostCredentialFile(context.credentialFile)).toBe(
        rotated.secret,
      );
      await expect(
        clientCall("recall", context.discoveryPath, {
          credentialProvider: () =>
            readHostCredentialFile(context.credentialFile),
        }),
      ).resolves.toBeDefined();
    });

    it.each([
      "missing",
      "empty",
      "corrupt",
      "json",
      "oversized",
      "file-mode",
      "directory-mode",
      "symlink",
      "directory-symlink",
    ] as const)(
      "denies %s credential files without path or secret leaks",
      async (kind) => {
        const context = await fixture();
        let target = context.credentialFile;
        if (kind === "missing") await rm(target);
        if (kind === "empty") await writeFile(target, "");
        if (kind === "corrupt")
          await writeFile(target, `bad ${context.issued.secret}`);
        if (kind === "json")
          await writeFile(
            target,
            JSON.stringify({ secret: context.issued.secret }),
          );
        if (kind === "oversized")
          await writeFile(target, context.issued.secret.repeat(100));
        if (kind === "file-mode") await chmod(target, 0o644);
        if (kind === "directory-mode") await chmod(context.root, 0o755);
        if (kind === "symlink") {
          target = path.join(context.root, "credential-link");
          await symlink(context.credentialFile, target);
        }
        if (kind === "directory-symlink") {
          const alias = path.join(context.root, "directory-link");
          await symlink(context.root, alias);
          target = path.join(alias, "host-credential");
        }
        const error = await clientCall("recall", context.discoveryPath, {
          credentialProvider: () => readHostCredentialFile(target),
        }).catch((failure: unknown) => failure);
        expect(error).toMatchObject({ code: "host_credential_unavailable" });
        expect(inspect(error)).not.toContain(context.issued.secret);
        expect(inspect(error)).not.toContain(context.root);
        expect(error).not.toHaveProperty("cause");
        expect(context.calls).toHaveLength(0);
      },
    );

    it.each(endpoints)(
      "sanitizes provider and HTTP errors including nested causes for %s",
      async (endpoint) => {
        const context = await fixture();
        const providerError = await clientCall(
          endpoint,
          context.discoveryPath,
          {
            credentialProvider: () => {
              throw new Error(
                `${context.credentialFile} ${context.issued.secret}`,
              );
            },
          },
        ).catch((error: unknown) => error);
        expect(providerError).toMatchObject({
          code: "host_credential_unavailable",
        });
        await context.store.revoke({
          installationId: context.receipt.installationId,
          expectedRevision: context.receipt.revision,
        });
        const transportError = await clientCall(
          endpoint,
          context.discoveryPath,
          { credentialProvider: () => context.issued.secret },
        ).catch((error: unknown) => error);
        expect(transportError).toMatchObject({
          code: "host_authorization_required",
        });
        for (const error of [providerError, transportError]) {
          expect(inspect(error)).not.toContain(context.issued.secret);
          expect(inspect(error)).not.toContain(context.credentialFile);
          expect(JSON.stringify(error)).not.toContain(context.issued.secret);
          expect(error).not.toHaveProperty("cause");
        }
      },
    );

    it("does not read environment credentials from the library", async () => {
      const context = await fixture();
      vi.stubEnv("ONE_MEMORY_HOST_CREDENTIAL_FILE", context.credentialFile);
      await expect(
        clientCall("recall", context.discoveryPath),
      ).rejects.toMatchObject({ code: "host_authorization_required" });
      expect(context.calls).toHaveLength(0);
    });

    it("never follows a broker redirect with a transport secret", async () => {
      const context = await fixture();
      const requests: IncomingHttpHeaders[] = [];
      const server = createServer((request, response) => {
        requests.push(request.headers);
        if (request.url === "/health") {
          response.end(
            JSON.stringify({
              status: "ok",
              service: "one-memory-recall-broker",
              instance_id: "redirect-fixture",
            }),
          );
        } else {
          response.statusCode = 307;
          response.setHeader("Location", `${context.baseUrl}/recall`);
          response.end();
        }
      });
      await new Promise<void>((resolve) =>
        server.listen(0, "127.0.0.1", resolve),
      );
      closers.push(
        () => new Promise<void>((resolve) => server.close(() => resolve())),
      );
      const discovery = JSON.parse(
        await readFile(context.discoveryPath, "utf8"),
      );
      discovery.port = (server.address() as AddressInfo).port;
      discovery.instance_id = "redirect-fixture";
      await writeFile(context.discoveryPath, JSON.stringify(discovery));
      const error = await clientCall("recall", context.discoveryPath, {
        credentialProvider: () => context.issued.secret,
      }).catch((failure: unknown) => failure);
      // A redirect is an answer: the broker is up and will not serve this
      // request, so the failure must not read as MemHub being stopped.
      expect(error).toMatchObject({ code: "broker_request_failed" });
      expect(context.calls).toHaveLength(0);
      expect(requests).toHaveLength(2);
      expect(requests[0].authorization).toBeUndefined();
      expect(requests[1].authorization).toBe(`Bearer ${context.issued.secret}`);
      expect(inspect(error)).not.toContain(context.issued.secret);
    });

    it("BUG-15: answers with only the tools the grant allows", async () => {
      const context = await fixture({ allowedTools: ["om_recall", "om_ask"] });
      const response = await axios.get(`${context.baseUrl}/granted-tools`, {
        headers: { Authorization: `Bearer ${context.issued.secret}` },
        proxy: false,
        maxRedirects: 0,
        timeout: 5_000,
        validateStatus: () => true,
      });
      expect(response.status).toBe(200);
      expect(response.data.allowed_tools).toEqual(["om_recall", "om_ask"]);
    });

    it("BUG-15: will not say what an unrecognised secret may use", async () => {
      const context = await fixture();
      const response = await axios.get(`${context.baseUrl}/granted-tools`, {
        headers: { Authorization: "Bearer not-a-real-secret" },
        proxy: false,
        maxRedirects: 0,
        timeout: 5_000,
        validateStatus: () => true,
      });
      expect(response.status).toBe(401);
      expect(response.data.allowed_tools).toBeUndefined();
    });

    it("does not blame a stopped MemHub for a request the broker answered", async () => {
      const context = await fixture({
        onCore: async (_call, response) => {
          response.statusCode = 500;
          response.end("core exploded");
        },
      });
      const error = await clientCall("recall", context.discoveryPath, {
        credentialProvider: () => context.issued.secret,
      }).catch((failure: unknown) => failure);
      // A broker that answered is a MemHub that is running. Saying otherwise
      // sends someone to restart something that is already up.
      expect(error).toMatchObject({ code: "broker_request_failed" });
    });

    it.each(["present", "missing", "corrupt", "unset"] as const)(
      "wires ONE_MEMORY_HOST_CREDENTIAL_FILE only in built MCP entry: %s",
      async (state) => {
        const context = await fixture();
        if (state === "missing") await rm(context.credentialFile);
        if (state === "corrupt")
          await writeFile(
            context.credentialFile,
            `broken ${context.issued.secret}`,
          );
        const child = spawn(process.execPath, [path.resolve("dist/mcp.cjs")], {
          cwd: context.root,
          env: {
            PATH: process.env.PATH,
            HOME: context.root,
            ONE_MEMORY_BROKER_DISCOVERY: context.discoveryPath,
            ...(state === "unset"
              ? {}
              : { ONE_MEMORY_HOST_CREDENTIAL_FILE: context.credentialFile }),
          },
          stdio: ["pipe", "pipe", "pipe"],
        });
        const exited = once(child, "exit");
        let stderr = "";
        child.stderr.on("data", (chunk: Buffer) => {
          stderr += chunk.toString();
        });
        const reader = createInterface({ input: child.stdout });
        const responses: unknown[] = [];
        const finished = deferred();
        const timer = setTimeout(
          () => finished.reject(new Error("MCP fixture timeout")),
          10_000,
        );
        reader.on("line", (line) => {
          responses.push(JSON.parse(line));
          if (responses.length === endpoints.length) finished.resolve();
        });
        try {
          for (const [index, endpoint] of endpoints.entries())
            child.stdin.write(
              `${JSON.stringify({ jsonrpc: "2.0", id: index, method: "tools/call", params: { name: tools[endpoint], arguments: body(endpoint) } })}\n`,
            );
          await finished.promise;
          for (const response of responses) {
            expect(
              (response as { result: { isError?: boolean } }).result.isError ===
                true,
            ).toBe(state !== "present");
          }
          expect(context.calls).toHaveLength(
            state === "present" ? endpoints.length : 0,
          );
          expect(JSON.stringify(responses)).not.toContain(
            context.issued.secret,
          );
          expect(JSON.stringify(responses)).not.toContain(
            context.credentialFile,
          );
          expect(stderr).not.toContain(context.issued.secret);
          expect(stderr).not.toContain(context.credentialFile);
          if (state === "present")
            for (const call of context.calls)
              expect(call.headers["x-one-memory-installation-id"]).toBe(
                context.receipt.installationId,
              );
        } finally {
          clearTimeout(timer);
          reader.close();
          child.kill();
          await exited;
        }
      },
      15_000,
    );
  },
);
