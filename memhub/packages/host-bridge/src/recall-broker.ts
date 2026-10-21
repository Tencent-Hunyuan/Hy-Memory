import { createHash, randomUUID, timingSafeEqual } from "node:crypto";
import {
  createServer,
  type IncomingMessage,
  type ServerResponse,
} from "node:http";
import type { AddressInfo } from "node:net";
import type {
  AskMemoryRequest,
  AskMemoryResponse,
  ContextSearchRequest,
  ContextSearchResponse,
  ConversationSearchResponse,
  RecallReceipt,
  RecallRequest,
} from "@one-memory/contracts";
import axios from "axios";
import type { CallEventDeliveryRequest } from "./broker-client.js";
import {
  authorizeContext,
  contextResponseInvalidField,
  isContextResponseAuthorized,
  isContextResponseForRequest,
  isContextSearchRequest,
} from "./context-protocol.js";
import { isConnectionControlError } from "./control-errors.js";
import { type BrokerDiscovery, writeDiscovery } from "./discovery.js";
import {
  HostGrantError,
  type HostGrantStore,
  type HostReadTool,
  type VerifiedHostIdentity,
} from "./host-grants.js";
import {
  SourceNotificationDispatcher,
  SourceNotificationError,
  type SourceNotificationHandler,
} from "./source-notifications.js";

const LOOPBACK_HOST = "127.0.0.1";
const DEFAULT_PORT = 18_765;
const MAX_BODY_BYTES = 64 * 1024;
const CONNECTION_DIAGNOSTIC_SUMMARY_MS = 5 * 60 * 1000;
const DELIVERY_BINDING_TTL_MS = 60_000;
const CALL_EVENT_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const HOST_SESSION_ID = /^[A-Za-z0-9][A-Za-z0-9_:-]{0,127}$/;
const PAIRING_FAILURE_WINDOW_MS = 60_000;
const MAX_PAIRING_FAILURES_PER_WINDOW = 10;

export interface ConnectionControlRequest {
  schema_version: 1;
  instance_id: string;
  action: "capability" | "preview" | "execute" | "receipt" | "host" | "pairing";
  input: Record<string, unknown>;
}

export interface RecallBrokerOptions {
  coreBaseUrl: string;
  coreApiToken: string;
  discoveryPath: string;
  instanceId: string;
  preferredPort?: number;
  /** Reader process base URL. When set, recall/context reads try it first
   *  and fall back to Core on failure or timeout. */
  readerBaseUrl?: string | null;
  now?: () => number;
  /** Production must supply a store. Omission retains legacy standalone mode
   * only; this option does not replace Core scope/source/content enforcement. */
  hostGrantStore?: HostGrantStore;
  /** Optional complementary sync trigger. Never inherits legacy read authority. */
  sourceNotificationHandler?: SourceNotificationHandler;
  /** Runtime-owned management only; never accessible using host read credentials. */
  connectionControl?(request: ConnectionControlRequest): Promise<unknown>;
  /** Notified when a fault occurs AFTER a successful start -- an event-handler
   * throw or a rejected request handler. The broker keeps running; this lets the
   * owning Runtime degrade instead of crashing. Must never be allowed to throw. */
  onFault?(code: string): void;
  diagnostics?(event: {
    level: "info" | "warn" | "error";
    component: string;
    event: string;
    outcome: string;
    trace_id?: string;
    instance_id?: string;
    duration_ms?: number;
    error_code?: string;
    error_type?: string;
    details?: Record<string, string | number | boolean | null>;
  }): Promise<void>;
}

export class RecallBroker {
  private readonly options: RecallBrokerOptions;
  private server: ReturnType<typeof createServer> | null = null;
  private activePort: number | null = null;
  private sourceNotifications: SourceNotificationDispatcher | null = null;
  private readonly connectionDiagnosticState = new Map<string, string>();
  private readonly connectionDiagnosticSummary = new Map<
    string,
    { startedAt: number; count: number; errorCount: number }
  >();
  private readonly deliveryBindings = new Map<
    string,
    {
      installationId: string;
      hostId: string;
      revision: number;
      expiresAt: number;
    }
  >();

  private readerCircuitOpenUntil = 0;
  private pairingFailureWindowStartedAt = 0;
  private pairingFailureCount = 0;

  constructor(options: RecallBrokerOptions) {
    this.options = Object.freeze({ ...options });
  }

  private readerAvailable(): boolean {
    return (
      Boolean(this.options.readerBaseUrl) &&
      Date.now() >= this.readerCircuitOpenUntil
    );
  }

  private openReaderCircuit(): void {
    this.readerCircuitOpenUntil = Date.now() + 30_000;
  }

  /** Try a read against the Reader process; fall back to Core on any failure. */
  private async readViaReader<T>(
    path: string,
    body: unknown,
    headers: Record<string, string>,
    timeout_ms: number,
    fallback: () => Promise<T>,
    signal?: AbortSignal,
  ): Promise<T> {
    signal?.throwIfAborted();
    if (!this.readerAvailable()) return fallback();
    try {
      const response = await axios.post<T>(
        `${this.options.readerBaseUrl}${path}`,
        body,
        {
          headers,
          proxy: false,
          maxRedirects: 0,
          timeout: Math.min(timeout_ms, 1_500),
          signal,
        },
      );
      return response.data;
    } catch (error) {
      // Caller cancellation is not a Reader failure and must not dispatch a
      // second request through Core after the first request was aborted.
      if (signal?.aborted) throw error;
      this.openReaderCircuit();
      return fallback();
    }
  }

  get port(): number {
    if (this.activePort === null) {
      throw new Error("MemHub recall broker is not running");
    }
    return this.activePort;
  }

  async start(): Promise<BrokerDiscovery> {
    if (this.server) {
      throw new Error("MemHub recall broker is already running");
    }
    const server = createServer((request, response) => {
      // A handler rejection must not become an unhandled rejection: with no
      // process-level guard that terminates the whole Runtime process, which is
      // exactly the "one broker fault kills the daemon" failure. Capture it as
      // an observable fault and try to answer the request honestly.
      void this.handle(request, response).catch((error: unknown) => {
        this.reportFault("broker_request_handler_failed");
        if (!response.headersSent && !response.destroyed) {
          sendJson(response, 500, { error: "broker_request_failed" });
        } else if (!response.writableEnded) {
          response.destroy();
        }
        this.emit({
          level: "error",
          component: "host.broker",
          event: "broker.request_failed",
          outcome: "failed",
          instance_id: this.options.instanceId,
          error_code: "broker_request_handler_failed",
          error_type: error instanceof Error ? error.name : "unknown",
        });
      });
    });
    this.server = server;
    if (this.options.hostGrantStore && this.options.sourceNotificationHandler) {
      this.sourceNotifications = new SourceNotificationDispatcher(
        this.options.hostGrantStore,
        this.options.sourceNotificationHandler,
        {
          observe: (value) =>
            this.emit({
              level: "info",
              component: "host.hook",
              event: "host_hook.completed",
              outcome: value.outcome,
              instance_id: this.options.instanceId,
              details: {
                action: "source_changed",
                reason: value.reason ?? "synchronized",
              },
            }),
        },
      );
    }

    const preferredPort = this.options.preferredPort ?? DEFAULT_PORT;
    try {
      await listen(server, preferredPort);
    } catch (error) {
      if (!isAddressInUse(error)) {
        this.server = null;
        throw error;
      }
      await listen(server, 0);
    }

    this.activePort = (server.address() as AddressInfo).port;
    const discovery = this.discovery("running", this.activePort);
    try {
      await writeDiscovery(this.options.discoveryPath, discovery);
    } catch (error) {
      await close(server);
      this.server = null;
      this.activePort = null;
      throw error;
    }
    return discovery;
  }

  async stop(): Promise<void> {
    await this.sourceNotifications?.shutdown();
    const server = this.server;
    if (server) await close(server);
    this.server = null;
    this.activePort = null;
    this.sourceNotifications = null;
    this.deliveryBindings.clear();
    await writeDiscovery(
      this.options.discoveryPath,
      this.discovery("stopped", null),
    );
  }

  private discovery(
    status: BrokerDiscovery["status"],
    port: number | null,
  ): BrokerDiscovery {
    return {
      schema_version: 1,
      service: "one-memory-recall-broker",
      status,
      host: LOOPBACK_HOST,
      port,
      instance_id: this.options.instanceId,
      pid: process.pid,
      updated_at: new Date().toISOString(),
    };
  }

  private async handle(
    request: IncomingMessage,
    response: ServerResponse,
  ): Promise<void> {
    setSecurityHeaders(response);
    if (!isLoopback(request.socket.remoteAddress)) {
      sendJson(response, 403, { error: "loopback clients only" });
      return;
    }
    if (request.method === "GET" && request.url === "/health") {
      sendJson(response, 200, {
        status: "ok",
        service: "one-memory-recall-broker",
        instance_id: this.options.instanceId,
      });
      return;
    }
    if (request.method === "GET" && request.url === "/granted-tools") {
      await this.grantedTools(request, response);
      return;
    }
    if (request.method !== "POST") {
      sendJson(response, 404, { error: "not found" });
      return;
    }
    const traceId = requestTraceId(request);
    request.headers["x-one-memory-trace-id"] = traceId;
    response.setHeader("X-One-Memory-Trace-Id", traceId);
    const operation =
      request.url === "/control/connections"
        ? null
        : brokerOperation(request.url);
    const started = Date.now();
    if (operation && operation !== "recall") {
      this.emit({
        level: "info",
        component: "host.broker",
        event: `${operation}.started`,
        outcome: "started",
        trace_id: traceId,
        instance_id: this.options.instanceId,
      });
      response.once("finish", () => {
        this.emit({
          level:
            response.statusCode >= 500
              ? "error"
              : response.statusCode >= 400
                ? "warn"
                : "info",
          component: "host.broker",
          event: `${operation}.completed`,
          outcome: response.statusCode < 400 ? "succeeded" : "failed",
          trace_id: traceId,
          instance_id: this.options.instanceId,
          duration_ms: Date.now() - started,
          ...(response.statusCode >= 400
            ? { error_code: `broker_http_${response.statusCode}` }
            : {}),
          details: { status: String(response.statusCode) },
        });
      });
    }
    if (request.url === "/control/connections") {
      await this.controlConnections(request, response);
      return;
    }
    if (request.url === "/pair") {
      await this.exchangePairing(request, response);
      return;
    }
    if (request.url === "/source-changed") {
      await this.notifySourceChanged(request, response);
      return;
    }
    if (
      request.headers["content-type"]?.split(";", 1)[0]?.trim() !==
        "application/json" ||
      (this.options.hostGrantStore === undefined &&
        request.headers["x-one-memory-client"] !== "cursor-mcp")
    ) {
      sendJson(response, 415, { error: "invalid client request" });
      return;
    }

    if (request.url === "/recall") {
      await this.proxyRecall(request, response);
      return;
    }
    if (request.url === "/context") {
      await this.proxyContext(request, response);
      return;
    }
    if (request.url === "/diagnostics/trace") {
      try {
        const body = await readJsonBody(request);
        if (
          !body ||
          typeof body !== "object" ||
          Array.isArray(body) ||
          Object.keys(body).length !== 1 ||
          !("request_id" in body || "trace_id" in body)
        ) {
          sendJson(response, 400, { error: "invalid trace query" });
          return;
        }
        const authorized = await this.authorizeHost(
          request,
          "om_diagnostic_trace",
          "all-apps",
        );
        const result = await axios.post(
          `${this.options.coreBaseUrl}/api/v1/diagnostics/trace`,
          body,
          {
            headers: authorized.headers,
            timeout: 5_000,
            proxy: false,
            maxRedirects: 0,
          },
        );
        await authorized.verifyBeforeRelease();
        sendJson(response, 200, result.data);
      } catch (error) {
        if (sendAuthorizationError(response, error)) return;
        if (error instanceof BodyReadError) {
          sendJson(response, error.status, { error: error.message });
          return;
        }
        sendJson(response, 502, {
          error: "MemHub diagnostic trace is unavailable",
        });
      }
      return;
    }
    if (request.url === "/boundary/peek" || request.url === "/boundary/ack") {
      await this.proxyBoundary(request, response);
      return;
    }
    if (request.url === "/call-events/delivery") {
      await this.proxyDelivery(request, response);
      return;
    }
    if (request.url === "/ask") {
      await this.proxyAsk(request, response);
      return;
    }
    if (request.url === "/conversations") {
      await this.proxyConversations(request, response);
      return;
    }
    if (request.url === "/matters") {
      try {
        const body = await readJsonBody(request);
        if (
          !body ||
          typeof body !== "object" ||
          Array.isArray(body) ||
          Object.keys(body).some((key) => key !== "query") ||
          !("query" in body) ||
          typeof body.query !== "string" ||
          body.query.trim().length < 1 ||
          body.query.length > 512
        ) {
          sendJson(response, 400, { error: "invalid matter query" });
          return;
        }
        const authorized = await this.authorizeHost(
          request,
          "om_matters",
          "all-apps",
        );
        const result = await axios.post(
          `${this.options.coreBaseUrl}/api/v1/matters/context/search`,
          body,
          {
            headers: authorized.headers,
            proxy: false,
            maxRedirects: 0,
            timeout: 15_000,
          },
        );
        await authorized.verifyBeforeRelease();
        sendJson(response, 200, result.data);
      } catch (error) {
        if (sendAuthorizationError(response, error)) return;
        if (error instanceof BodyReadError) {
          sendJson(response, error.status, { error: error.message });
          return;
        }
        sendJson(response, 502, {
          error: "MemHub matter context is unavailable",
        });
      }
      return;
    }
    sendJson(response, 404, { error: "not found" });
  }

  /** Which read tools this installation's grant allows.
   *
   * The list a host shows used to be the same for every installation, so it
   * offered tools the grant would refuse on the call. Reading the grant needs
   * no tool of its own: an installation is authenticated, not authorized
   * against a tool, because the question is what it may use at all. */
  private async grantedTools(
    request: IncomingMessage,
    response: ServerResponse,
  ) {
    const store = this.options.hostGrantStore;
    if (store === undefined) {
      sendJson(response, 404, { error: "host_grant_store_unavailable" });
      return;
    }
    const authorization = request.headers.authorization;
    const authorizationCount = request.rawHeaders.filter(
      (value, index) =>
        index % 2 === 0 && value.toLowerCase() === "authorization",
    ).length;
    if (
      authorizationCount !== 1 ||
      typeof authorization !== "string" ||
      !/^Bearer omhg_v1_[A-Za-z0-9_-]{43}$/i.test(authorization)
    ) {
      sendJson(response, 401, { error: "host_grant_authentication_failed" });
      return;
    }
    try {
      const identity = await store.authenticate(authorization.slice(7));
      sendJson(response, 200, {
        installation_id: identity.installationId,
        revision: identity.revision,
        allowed_tools: [...identity.allowedTools],
      });
    } catch (error) {
      if (sendAuthorizationError(response, error)) return;
      sendJson(response, 500, { error: "host_grant_store_unavailable" });
    }
  }

  private async exchangePairing(
    request: IncomingMessage,
    response: ServerResponse,
  ): Promise<void> {
    const store = this.options.hostGrantStore;
    if (store === undefined) {
      sendJson(response, 404, { error: "host_pairing_unavailable" });
      return;
    }
    if (
      request.headers.origin !== undefined ||
      Object.keys(request.headers).some((key) =>
        key.startsWith("sec-fetch-"),
      ) ||
      request.headers["content-type"]?.split(";", 1)[0]?.trim() !==
        "application/json"
    ) {
      sendJson(response, 415, { error: "host_pairing_invalid" });
      return;
    }
    const now = Date.now();
    if (now - this.pairingFailureWindowStartedAt >= PAIRING_FAILURE_WINDOW_MS) {
      this.pairingFailureWindowStartedAt = now;
      this.pairingFailureCount = 0;
    }
    if (this.pairingFailureCount >= MAX_PAIRING_FAILURES_PER_WINDOW) {
      sendJson(response, 429, { error: "host_pairing_rate_limited" });
      return;
    }
    try {
      const body = await readJsonBody(request, 10_000);
      if (
        !body ||
        typeof body !== "object" ||
        Array.isArray(body) ||
        Object.keys(body).length !== 1 ||
        !("code" in body) ||
        typeof body.code !== "string" ||
        !/^\d{6}$/.test(body.code)
      ) {
        this.pairingFailureCount++;
        sendJson(response, 400, { error: "host_pairing_invalid" });
        return;
      }
      const issued = await store.exchangePairing(body.code);
      this.pairingFailureCount = 0;
      sendJson(response, 200, {
        schema_version: 1,
        host_id: issued.receipt.hostId,
        installation_id: issued.receipt.installationId,
        credential_type: "bearer",
        credential: issued.secret,
      });
    } catch (error) {
      this.pairingFailureCount++;
      if (
        error instanceof HostGrantError &&
        error.code === "host_pairing_invalid"
      ) {
        sendJson(response, 401, { error: "host_pairing_invalid" });
        return;
      }
      if (error instanceof BodyReadError) {
        sendJson(response, error.status, { error: "host_pairing_invalid" });
        return;
      }
      sendJson(response, 500, { error: "host_pairing_unavailable" });
    }
  }

  private async controlConnections(
    request: IncomingMessage,
    response: ServerResponse,
  ) {
    const started = Date.now();
    let action: ConnectionControlRequest["action"] | null = null;
    const authorization = request.headers.authorization;
    const authorizationCount = request.rawHeaders.filter(
      (value, index) =>
        index % 2 === 0 && value.toLowerCase() === "authorization",
    ).length;
    const expected = createHash("sha256")
      .update(`Bearer ${this.options.coreApiToken}`)
      .digest();
    const actual = createHash("sha256")
      .update(authorization ?? "")
      .digest();
    if (
      authorizationCount !== 1 ||
      !this.options.coreApiToken ||
      !timingSafeEqual(expected, actual) ||
      request.headers.origin !== undefined ||
      Object.keys(request.headers).some((key) => key.startsWith("sec-fetch-"))
    ) {
      sendJson(response, 403, { error: "connection_control_forbidden" });
      return;
    }
    if (!this.options.connectionControl) {
      sendJson(response, 404, { error: "connection_control_unavailable" });
      return;
    }
    if (
      request.headers["content-type"]?.split(";", 1)[0]?.trim() !==
      "application/json"
    ) {
      sendJson(response, 415, { error: "connection_control_invalid" });
      return;
    }
    try {
      const body = await readJsonBody(request, 10000);
      if (
        !body ||
        typeof body !== "object" ||
        Array.isArray(body) ||
        Object.keys(body).sort().join(",") !==
          "action,input,instance_id,schema_version" ||
        !("schema_version" in body) ||
        body.schema_version !== 1 ||
        !("instance_id" in body) ||
        body.instance_id !== this.options.instanceId ||
        !("action" in body) ||
        ![
          "capability",
          "preview",
          "execute",
          "receipt",
          "host",
          "pairing",
        ].includes(String(body.action)) ||
        !("input" in body) ||
        !body.input ||
        typeof body.input !== "object" ||
        Array.isArray(body.input)
      ) {
        sendJson(response, 400, { error: "connection_control_invalid" });
        return;
      }
      const value = body as ConnectionControlRequest;
      action = value.action;
      if (!["capability", "receipt", "host"].includes(value.action)) {
        this.emit({
          level: "info",
          component: "host.broker",
          event: "connection_control.started",
          outcome: "started",
          instance_id: this.options.instanceId,
          details: { action: value.action },
        });
      }
      const result = await this.options.connectionControl(value);
      const envelope = {
        schema_version: 1,
        instance_id: this.options.instanceId,
        action: value.action,
        result,
      };
      if (Buffer.byteLength(JSON.stringify(envelope)) > 1024 * 1024)
        throw Error("connection_control_response_limit");
      if (!response.destroyed) sendJson(response, 200, envelope);
      this.recordConnectionDiagnostic(value.action, false);
      if (!["capability", "receipt", "host"].includes(value.action)) {
        this.emit({
          level: "info",
          component: "host.broker",
          event: "connection_control.completed",
          outcome: "succeeded",
          instance_id: this.options.instanceId,
          duration_ms: Date.now() - started,
          details: { action: value.action, status: "200" },
        });
      }
    } catch (error) {
      const code =
        error instanceof Error && isConnectionControlError(error.message)
          ? error.message
          : "connection_control_unavailable";
      const isKnown =
        error instanceof Error && isConnectionControlError(error.message);
      if (!isKnown) {
        // Whitelist-out-of-range errors bypass the control-plane dedup:
        // emit unconditionally so an unexpected exception (the D43
        // condition) leaves a trace even when the response code stays the
        // same. Known control-plane errors keep their existing dedup path.
        this.emit({
          level: "error",
          component: "host.broker",
          event: "connection_control.exception",
          outcome: "failed",
          instance_id: this.options.instanceId,
          details: {
            action: action ?? "invalid",
            code,
            error_type:
              error instanceof Error ? error.constructor.name : typeof error,
            message:
              error instanceof Error
                ? error.message.slice(0, 256)
                : String(error).slice(0, 256),
            stack_origin:
              error instanceof Error && error.stack
                ? (error.stack.split("\n").at(1)?.trim() ?? "").slice(0, 256)
                : null,
          },
        });
      }
      const fingerprint = `error:${code}`;
      const key = action ?? "invalid";
      if (action) this.recordConnectionDiagnostic(action, true);
      if (this.connectionDiagnosticState.get(key) !== fingerprint) {
        this.connectionDiagnosticState.set(key, fingerprint);
        this.emit({
          level: "error",
          component: "host.broker",
          event: "connection_control.failed",
          outcome: "failed",
          instance_id: this.options.instanceId,
          duration_ms: Date.now() - started,
          error_code: code,
          details: { action: key },
        });
      }
      if (!response.destroyed)
        sendJson(
          response,
          error instanceof BodyReadError ? error.status : 503,
          {
            error: code,
          },
        );
    }
  }

  private async notifySourceChanged(
    request: IncomingMessage,
    response: ServerResponse,
  ): Promise<void> {
    try {
      if (!this.sourceNotifications)
        throw new SourceNotificationError(
          "source_notification_unavailable",
          503,
        );
      if (
        request.headers["content-type"]?.split(";", 1)[0]?.trim() !==
        "application/json"
      )
        throw new SourceNotificationError("source_notification_invalid", 400);
      const authorization = request.headers.authorization;
      const count = request.rawHeaders.filter(
        (value, index) =>
          index % 2 === 0 && value.toLowerCase() === "authorization",
      ).length;
      if (
        count !== 1 ||
        typeof authorization !== "string" ||
        !/^Bearer omhg_v1_[A-Za-z0-9_-]{43}$/i.test(authorization)
      )
        throw new SourceNotificationError(
          "host_grant_authentication_failed",
          401,
        );
      const body = await readJsonBody(request);
      const result = await this.sourceNotifications.dispatch(
        authorization.slice(7),
        body,
      );
      sendJson(response, 200, result);
    } catch (error) {
      const safe =
        error instanceof SourceNotificationError
          ? new SourceNotificationError(error.code, error.status)
          : new SourceNotificationError(
              error instanceof BodyReadError
                ? "source_notification_invalid"
                : "source_notification_unavailable",
              error instanceof BodyReadError ? 400 : 503,
            );
      this.emit({
        level: safe.status >= 500 ? "error" : "warn",
        component: "host.hook",
        event: "host_hook.failed",
        outcome: "skipped",
        instance_id: this.options.instanceId,
        error_code: safe.code,
        details: { action: "source_changed", reason: safe.code },
      });
      sendJson(response, safe.status, { error: safe.code });
    }
  }

  private async proxyContext(
    request: IncomingMessage,
    response: ServerResponse,
  ): Promise<void> {
    const abort = new AbortController();
    const onClose = () => {
      if (!response.writableEnded) abort.abort();
    };
    response.once("close", onClose);
    try {
      const body = await readJsonBody(request);
      if (!isContextSearchRequest(body)) {
        sendJson(response, 400, { error: "invalid_context_request" });
        return;
      }
      const authorized = await this.authorizeHost(request, body, "all-apps");
      const result = {
        data: await this.readViaReader<unknown>(
          "/api/v1/context/search",
          body,
          authorized.headers,
          (body.timeout_ms ?? 2000) + 1000,
          async () => {
            const r = await axios.post(
              `${this.options.coreBaseUrl}/api/v1/context/search`,
              body,
              {
                headers: authorized.headers,
                proxy: false,
                maxRedirects: 0,
                timeout: (body.timeout_ms ?? 2000) + 1000,
                maxContentLength: 2 * 1024 * 1024,
                signal: abort.signal,
              },
            );
            await authorized.verifyBeforeRelease();
            return r.data;
          },
          abort.signal,
        ),
      };
      const invalidField = contextResponseInvalidField(result.data);
      const identity = authorized.identity;
      const responseFailure =
        invalidField ??
        (!isContextResponseForRequest(result.data, body)
          ? "request_or_budget"
          : !identity
            ? "host_identity"
            : !isContextResponseAuthorized(result.data, identity)
              ? "items.authorization"
              : null);
      if (responseFailure) {
        this.emit({
          level: "error",
          component: "host.context",
          event: "context.response_invalid",
          outcome: "failed",
          instance_id: this.options.instanceId,
          error_code: "invalid_context_response",
          details: { reason: responseFailure },
        });
        throw new Error("invalid_context_response");
      }
      if (!identity) throw new Error("invalid_context_response");
      const contextResult = result.data as ContextSearchResponse;
      await authorized.verifyBeforeRelease();
      if (
        identity.hostId === "workbuddy" &&
        CALL_EVENT_ID.test(contextResult.request_id)
      ) {
        this.rememberDeliveryBinding(contextResult.request_id, identity);
      }
      if (!abort.signal.aborted) sendJson(response, 200, contextResult);
    } catch (error) {
      if (abort.signal.aborted) return;
      if (sendAuthorizationError(response, error)) return;
      if (error instanceof BodyReadError) {
        sendJson(response, error.status, { error: error.message });
        return;
      }
      if (
        axios.isAxiosError(error) &&
        [404, 405, 501].includes(error.response?.status ?? 0)
      ) {
        sendJson(response, 501, { error: "context_upgrade_required" });
        return;
      }
      sendJson(response, 502, { error: "context_unavailable" });
    } finally {
      response.off("close", onClose);
    }
  }

  private async proxyBoundary(
    request: IncomingMessage,
    response: ServerResponse,
  ): Promise<void> {
    try {
      const body = await readJsonBody(request);
      const authorized = await this.authorizeHost(
        request,
        "om_recall",
        "all-apps",
      );
      if (authorized.identity?.hostId !== "workbuddy")
        throw new HostGrantError("host_grant_scope_denied");
      const endpoint = request.url === "/boundary/ack" ? "ack" : "peek";
      const result = await axios.post(
        `${this.options.coreBaseUrl}/api/v1/boundary/${endpoint}`,
        body,
        {
          headers: authorized.headers,
          proxy: false,
          maxRedirects: 0,
          timeout: 700,
        },
      );
      await authorized.verifyBeforeRelease();
      sendJson(response, 200, result.data);
    } catch (error) {
      if (sendAuthorizationError(response, error)) return;
      if (error instanceof BodyReadError) {
        sendJson(response, error.status, { error: error.message });
        return;
      }
      sendJson(response, 502, { error: "boundary_unavailable" });
    }
  }

  private rememberDeliveryBinding(
    requestId: string,
    identity: VerifiedHostIdentity,
  ): void {
    const now = this.options.now?.() ?? Date.now();
    for (const [id, binding] of this.deliveryBindings) {
      if (binding.expiresAt <= now) this.deliveryBindings.delete(id);
    }
    this.deliveryBindings.set(requestId, {
      installationId: identity.installationId,
      hostId: identity.hostId,
      revision: identity.revision,
      expiresAt: now + DELIVERY_BINDING_TTL_MS,
    });
  }

  private async proxyDelivery(
    request: IncomingMessage,
    response: ServerResponse,
  ): Promise<void> {
    try {
      const body = await readJsonBody(request);
      if (!isDeliveryRequest(body)) {
        sendJson(response, 400, { error: "invalid_delivery_request" });
        return;
      }
      const authorized = await this.authenticateInstalledHost(request);
      const now = this.options.now?.() ?? Date.now();
      const binding = this.deliveryBindings.get(body.request_id);
      if (
        !binding ||
        binding.expiresAt <= now ||
        binding.hostId !== "workbuddy" ||
        authorized.identity.hostId !== binding.hostId ||
        authorized.identity.installationId !== binding.installationId ||
        authorized.identity.revision !== binding.revision
      ) {
        this.deliveryBindings.delete(body.request_id);
        sendJson(response, 403, { error: "delivery_binding_denied" });
        return;
      }
      this.deliveryBindings.delete(body.request_id);
      await authorized.verifyBeforeRelease();
      const result = await axios.post(
        `${this.options.coreBaseUrl}/api/v1/call-events/${encodeURIComponent(body.request_id)}/delivery`,
        {
          delivery: body.delivery,
          host_session_id: body.host_session_id,
          injection_elapsed_ms: body.injection_elapsed_ms,
        },
        {
          headers: authorized.headers,
          proxy: false,
          maxRedirects: 0,
          timeout: 5_000,
        },
      );
      await authorized.verifyBeforeRelease();
      sendJson(response, 200, result.data);
    } catch (error) {
      if (sendAuthorizationError(response, error)) return;
      if (error instanceof BodyReadError) {
        sendJson(response, error.status, { error: error.message });
        return;
      }
      sendJson(response, 502, { error: "delivery_unavailable" });
    }
  }

  private async authenticateInstalledHost(request: IncomingMessage): Promise<{
    headers: Record<string, string>;
    identity: VerifiedHostIdentity;
    verifyBeforeRelease: () => Promise<void>;
  }> {
    const store = this.options.hostGrantStore;
    if (store === undefined)
      throw new HostGrantError("host_grant_store_unavailable");
    const authorization = request.headers.authorization;
    const headerCount = request.rawHeaders.filter(
      (value, index) =>
        index % 2 === 0 && value.toLowerCase() === "authorization",
    ).length;
    if (
      headerCount !== 1 ||
      typeof authorization !== "string" ||
      !/^Bearer omhg_v1_[A-Za-z0-9_-]{43}$/i.test(authorization)
    ) {
      throw new HostGrantError("host_grant_authentication_failed");
    }
    const secret = authorization.slice(7);
    try {
      const identity = await store.authenticate(secret);
      const headers = {
        Authorization: `Bearer ${this.options.coreApiToken}`,
        "Content-Type": "application/json",
        "x-one-memory-read-context": "external",
        "x-one-memory-host-id": identity.hostId,
        "x-one-memory-installation-id": identity.installationId,
        "x-one-memory-installation-revision": String(identity.revision),
        "x-one-memory-allowed-scopes": JSON.stringify(identity.allowedScopes),
        "x-one-memory-allowed-tools": JSON.stringify(identity.allowedTools),
        "x-one-memory-trace-id": requestTraceId(request),
      };
      return {
        headers,
        identity,
        verifyBeforeRelease: async () => {
          const current = await store.authenticate(secret);
          if (
            current.installationId !== identity.installationId ||
            current.hostId !== identity.hostId ||
            current.revision !== identity.revision
          ) {
            throw new HostGrantError("host_grant_revision_conflict");
          }
        },
      };
    } catch (error) {
      if (error instanceof HostGrantError) throw error;
      throw new HostGrantError("host_grant_store_unavailable");
    }
  }

  private async proxyRecall(
    request: IncomingMessage,
    response: ServerResponse,
  ): Promise<void> {
    try {
      const body = await readJsonBody(request);
      if (!isRecallRequest(body)) {
        sendJson(response, 400, { error: "invalid recall request" });
        return;
      }
      const authorized = await this.authorizeHost(
        request,
        "om_recall",
        body.scope ?? "all-apps",
      );
      const sourceId =
        authorized.identity?.hostId ?? legacyRecallSource(request);
      const traceId = requestTraceId(request);
      const started = Date.now();
      this.emit({
        level: "info",
        component: "host.broker",
        event: "recall.started",
        outcome: "started",
        trace_id: traceId,
        instance_id: this.options.instanceId,
        details: sourceId ? { source_id: sourceId } : undefined,
      });
      response.once("finish", () => {
        this.emit({
          level:
            response.statusCode >= 500
              ? "error"
              : response.statusCode >= 400
                ? "warn"
                : "info",
          component: "host.broker",
          event: "recall.completed",
          outcome: response.statusCode < 400 ? "succeeded" : "failed",
          trace_id: traceId,
          instance_id: this.options.instanceId,
          duration_ms: Date.now() - started,
          ...(response.statusCode >= 400
            ? { error_code: `broker_http_${response.statusCode}` }
            : {}),
          details: {
            status: String(response.statusCode),
            ...(sourceId ? { source_id: sourceId } : {}),
          },
        });
      });
      const coreResponse = await this.readViaReader<RecallReceipt>(
        "/api/v1/recall",
        body,
        authorized.headers,
        15_000,
        async () => {
          const result = await axios.post<RecallReceipt>(
            `${this.options.coreBaseUrl}/api/v1/recall`,
            body,
            {
              headers: authorized.headers,
              proxy: false,
              maxRedirects: 0,
              timeout: 15_000,
            },
          );
          await authorized.verifyBeforeRelease();
          return result.data;
        },
      );
      sendJson(response, 200, coreResponse);
    } catch (error) {
      if (sendAuthorizationError(response, error)) return;
      if (error instanceof BodyReadError) {
        sendJson(response, error.status, { error: error.message });
        return;
      }
      sendJson(response, 502, {
        error: "MemHub Core recall is unavailable",
      });
    }
  }

  /** Search the original conversations, not the memories drawn from them.
   *
   * An agent asked to pick a task back up needs what was actually said in the
   * other app. Recall cannot supply that: it returns governed memories, which
   * are conclusions, and a conclusion is not enough to resume work from.
   *
   * Read-only, and reads nothing that was not already imported. */
  private async proxyConversations(
    request: IncomingMessage,
    response: ServerResponse,
  ): Promise<void> {
    try {
      const body = await readJsonBody(request);
      if (!isConversationSearchRequest(body)) {
        sendJson(response, 400, { error: "invalid conversation request" });
        return;
      }
      const authorized = await this.authorizeHost(
        request,
        "om_conversations",
        "all-apps",
      );
      const coreResponse = await axios.get<ConversationSearchResponse>(
        `${this.options.coreBaseUrl}/api/v1/conversations/search`,
        {
          params: { query: body.query, limit: body.limit ?? 20 },
          headers: authorized.headers,
          proxy: false,
          maxRedirects: 0,
          timeout: 15_000,
        },
      );
      await authorized.verifyBeforeRelease();
      sendJson(response, 200, coreResponse.data);
    } catch (error) {
      if (sendAuthorizationError(response, error)) return;
      if (error instanceof BodyReadError) {
        sendJson(response, error.status, { error: error.message });
        return;
      }
      sendJson(response, 502, {
        error: "MemHub Core conversation search is unavailable",
      });
    }
  }

  private async proxyAsk(
    request: IncomingMessage,
    response: ServerResponse,
  ): Promise<void> {
    try {
      const body = await readJsonBody(request);
      if (!isAskRequest(body)) {
        sendJson(response, 400, { error: "invalid ask request" });
        return;
      }
      const authorized = await this.authorizeHost(
        request,
        "om_ask",
        body.scope ?? "all-apps",
      );
      // Ask may call a generation model; keep the broker timeout aligned with
      // the web client's 120s budget rather than the short recall budget.
      const coreResponse = await axios.post<AskMemoryResponse>(
        `${this.options.coreBaseUrl}/api/v1/memories/ask`,
        body,
        {
          headers: authorized.headers,
          proxy: false,
          maxRedirects: 0,
          timeout: 120_000,
        },
      );
      await authorized.verifyBeforeRelease();
      sendJson(response, 200, coreResponse.data);
    } catch (error) {
      if (sendAuthorizationError(response, error)) return;
      if (error instanceof BodyReadError) {
        sendJson(response, error.status, { error: error.message });
        return;
      }
      sendJson(response, 502, {
        error: "MemHub Core ask is unavailable",
      });
    }
  }

  private async authorizeHost(
    request: IncomingMessage,
    tool: HostReadTool | ContextSearchRequest,
    scope: string,
  ): Promise<{
    headers: Record<string, string>;
    verifyBeforeRelease: () => Promise<void>;
    identity?: VerifiedHostIdentity;
  }> {
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.options.coreApiToken}`,
      "Content-Type": "application/json",
      "x-one-memory-read-context": "external",
      "x-one-memory-trace-id": requestTraceId(request),
    };
    const store = this.options.hostGrantStore;
    if (store === undefined && typeof tool !== "string")
      throw new HostGrantError("host_grant_store_unavailable");
    if (store === undefined)
      return { headers, verifyBeforeRelease: async () => {} };
    const authorization = request.headers.authorization;
    const headerCount = request.rawHeaders.filter(
      (value, index) =>
        index % 2 === 0 && value.toLowerCase() === "authorization",
    ).length;
    if (
      headerCount !== 1 ||
      typeof authorization !== "string" ||
      !/^Bearer omhg_v1_[A-Za-z0-9_-]{43}$/i.test(authorization)
    ) {
      throw new HostGrantError("host_grant_authentication_failed");
    }
    const secret = authorization.slice(7);
    try {
      // This is only the explicit/default request scope. Core must intersect
      // workspace expansion and raw/source domains with these allowed grants.
      const identity =
        typeof tool === "string"
          ? await store.authorize(secret, { tool, scopes: [scope] })
          : await store.authenticate(secret);
      if (typeof tool !== "string") authorizeContext(identity, tool);
      headers["x-one-memory-installation-id"] = identity.installationId;
      headers["x-one-memory-host-id"] = identity.hostId;
      headers["x-one-memory-installation-revision"] = String(identity.revision);
      headers["x-one-memory-allowed-scopes"] = JSON.stringify(
        identity.allowedScopes,
      );
      headers["x-one-memory-allowed-tools"] = JSON.stringify(
        identity.allowedTools,
      );
      return {
        headers,
        identity,
        verifyBeforeRelease: async () => {
          try {
            const current = await store.authenticate(secret);
            if (
              current.installationId !== identity.installationId ||
              current.revision !== identity.revision
            ) {
              throw new HostGrantError("host_grant_revision_conflict");
            }
          } catch (error) {
            if (error instanceof HostGrantError) throw error;
            throw new HostGrantError("host_grant_store_unavailable");
          }
        },
      };
    } catch (error) {
      if (error instanceof HostGrantError) throw error;
      throw new HostGrantError("host_grant_store_unavailable");
    }
  }

  /** Fire and forget: no request, hook or poll ever waits for a log write. */
  private emit(
    event: Parameters<NonNullable<RecallBrokerOptions["diagnostics"]>>[0],
  ): void {
    try {
      this.options.diagnostics?.(event)?.catch(() => undefined);
    } catch {
      // A synchronous throw from the writer is dropped like an asynchronous one.
    }
  }

  /** Idempotently surface a post-start fault to the owning Runtime. Never
   * throws: it is called from error paths whose only alternative is a crash. */
  private reportFault(code: string): void {
    try {
      this.options.onFault?.(code);
    } catch {
      // A fault observer must not itself become a fault.
    }
  }

  private recordConnectionDiagnostic(
    action: ConnectionControlRequest["action"],
    failed: boolean,
  ): void {
    if (!["capability", "receipt", "host"].includes(action)) return;
    const now = this.options.now?.() ?? Date.now();
    const current = this.connectionDiagnosticSummary.get(action) ?? {
      startedAt: now,
      count: 0,
      errorCount: 0,
    };
    current.count += 1;
    if (failed) current.errorCount += 1;
    if (now - current.startedAt < CONNECTION_DIAGNOSTIC_SUMMARY_MS) {
      this.connectionDiagnosticSummary.set(action, current);
      return;
    }
    if (current.errorCount) {
      this.emit({
        level: "warn",
        component: "host.broker",
        event: "connection_control.summary",
        outcome: "observed",
        instance_id: this.options.instanceId,
        details: {
          action,
          count: current.count,
          error_count: current.errorCount,
        },
      });
    }
    this.connectionDiagnosticSummary.set(action, {
      startedAt: now,
      count: 0,
      errorCount: 0,
    });
  }
}

function requestTraceId(request: IncomingMessage): string {
  const value = request.headers["x-one-memory-trace-id"];
  if (
    typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(
      value,
    )
  )
    return value;
  return randomUUID();
}

function legacyRecallSource(request: IncomingMessage): string | undefined {
  const value = request.headers["x-one-memory-client"];
  if (typeof value !== "string") return undefined;
  const normalized = value.endsWith("-mcp") ? value.slice(0, -4) : value;
  return /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(normalized)
    ? normalized
    : undefined;
}

function isDeliveryRequest(value: unknown): value is CallEventDeliveryRequest {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const body = value as Record<string, unknown>;
  return (
    Object.keys(body).length === 4 &&
    CALL_EVENT_ID.test(String(body.request_id ?? "")) &&
    (body.delivery === "delivered" || body.delivery === "not_delivered") &&
    typeof body.host_session_id === "string" &&
    HOST_SESSION_ID.test(body.host_session_id) &&
    typeof body.injection_elapsed_ms === "number" &&
    Number.isFinite(body.injection_elapsed_ms) &&
    body.injection_elapsed_ms >= 0
  );
}

function brokerOperation(url: string | undefined): string | null {
  return (
    {
      "/recall": "recall",
      "/context": "context",
      "/call-events/delivery": "call_event_delivery",
      "/ask": "ask",
      "/conversations": "conversations",
      "/matters": "matters",
      "/source-changed": "source_notification",
      "/control/connections": "connection_control",
    }[url ?? ""] ?? null
  );
}

function sendAuthorizationError(
  response: ServerResponse,
  error: unknown,
): boolean {
  if (error instanceof HostGrantError) {
    if (error.code === "host_grant_authentication_failed") {
      sendJson(response, 401, { error: error.code });
    } else if (
      [
        "host_grant_not_approved",
        "host_grant_tool_denied",
        "host_grant_scope_denied",
        "host_grant_revision_conflict",
      ].includes(error.code)
    ) {
      sendJson(response, 403, { error: error.code });
    } else {
      sendJson(response, 503, { error: "host_grant_store_unavailable" });
    }
    return true;
  }
  if (axios.isAxiosError(error) && error.response?.status === 403) {
    sendJson(response, 403, { error: "external_recall_scope_denied" });
    return true;
  }
  return false;
}

/** Same shape Core's query string takes, so nothing here can widen it. */
export interface ConversationSearchQuery {
  query: string;
  limit?: number;
}

function isConversationSearchRequest(
  value: unknown,
): value is ConversationSearchQuery {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const request = value as Record<string, unknown>;
  const allowed = new Set(["query", "limit"]);
  if (Object.keys(request).some((key) => !allowed.has(key))) return false;
  return (
    typeof request.query === "string" &&
    request.query.length >= 1 &&
    request.query.length <= 512 &&
    (request.limit === undefined ||
      (Number.isInteger(request.limit) &&
        (request.limit as number) >= 1 &&
        (request.limit as number) <= 50))
  );
}

function isAskRequest(value: unknown): value is AskMemoryRequest {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const request = value as Record<string, unknown>;
  const allowed = new Set(["question", "scope", "workspace_path"]);
  if (Object.keys(request).some((key) => !allowed.has(key))) return false;
  return (
    typeof request.question === "string" &&
    request.question.length >= 1 &&
    request.question.length <= 2_000 &&
    (request.scope === undefined ||
      (typeof request.scope === "string" &&
        request.scope.length >= 1 &&
        request.scope.length <= 512)) &&
    (request.workspace_path === undefined ||
      (typeof request.workspace_path === "string" &&
        request.workspace_path.length >= 1 &&
        request.workspace_path.length <= 4_096))
  );
}

function isRecallRequest(value: unknown): value is RecallRequest {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const request = value as Record<string, unknown>;
  const allowed = new Set([
    "query",
    "scope",
    "limit",
    "character_budget",
    "timeout_ms",
    "workspace_path",
  ]);
  if (Object.keys(request).some((key) => !allowed.has(key))) return false;
  return (
    typeof request.query === "string" &&
    request.query.length >= 1 &&
    request.query.length <= 2_000 &&
    (request.scope === undefined ||
      (typeof request.scope === "string" &&
        request.scope.length >= 1 &&
        request.scope.length <= 512)) &&
    (request.limit === undefined ||
      (Number.isInteger(request.limit) &&
        (request.limit as number) >= 1 &&
        (request.limit as number) <= 20)) &&
    (request.character_budget === undefined ||
      (Number.isInteger(request.character_budget) &&
        (request.character_budget as number) >= 200 &&
        (request.character_budget as number) <= 20_000)) &&
    (request.timeout_ms === undefined ||
      (Number.isInteger(request.timeout_ms) &&
        (request.timeout_ms as number) >= 50 &&
        (request.timeout_ms as number) <= 30_000)) &&
    (request.workspace_path === undefined ||
      (typeof request.workspace_path === "string" &&
        request.workspace_path.length >= 1 &&
        request.workspace_path.length <= 4_096))
  );
}

class BodyReadError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function readJsonBody(
  request: IncomingMessage,
  deadlineMs?: number,
): Promise<unknown> {
  const deadline =
    deadlineMs === undefined
      ? undefined
      : setTimeout(() => {
          request.destroy(new BodyReadError(408, "request body deadline"));
        }, deadlineMs);
  deadline?.unref();
  const chunks: Buffer[] = [];
  let bytes = 0;
  try {
    for await (const chunk of request) {
      const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      bytes += buffer.length;
      if (bytes > MAX_BODY_BYTES) {
        throw new BodyReadError(413, "request body is too large");
      }
      chunks.push(buffer);
    }
    try {
      return JSON.parse(Buffer.concat(chunks).toString("utf8"));
    } catch {
      throw new BodyReadError(400, "request body is not valid JSON");
    }
  } finally {
    clearTimeout(deadline);
  }
}

function setSecurityHeaders(response: ServerResponse): void {
  response.setHeader("Cache-Control", "no-store");
  response.setHeader("Content-Security-Policy", "default-src 'none'");
  response.setHeader("X-Content-Type-Options", "nosniff");
  response.setHeader("X-Frame-Options", "DENY");
}

function sendJson(
  response: ServerResponse,
  status: number,
  value: unknown,
): void {
  response.statusCode = status;
  response.setHeader("Content-Type", "application/json; charset=utf-8");
  response.end(`${JSON.stringify(value)}\n`);
}

function isLoopback(address: string | undefined): boolean {
  return (
    address === LOOPBACK_HOST ||
    address === "::1" ||
    address === "::ffff:127.0.0.1"
  );
}

function isAddressInUse(error: unknown): boolean {
  return (
    error instanceof Error &&
    "code" in error &&
    (error as NodeJS.ErrnoException).code === "EADDRINUSE"
  );
}

function listen(
  server: ReturnType<typeof createServer>,
  port: number,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const onError = (error: Error): void => {
      server.off("listening", onListening);
      reject(error);
    };
    const onListening = (): void => {
      server.off("error", onError);
      resolve();
    };
    server.once("error", onError);
    server.once("listening", onListening);
    server.listen(port, LOOPBACK_HOST);
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
