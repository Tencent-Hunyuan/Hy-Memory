import { AsyncLocalStorage } from "node:async_hooks";
import { createHash, randomUUID } from "node:crypto";
import type { EventEmitter } from "node:events";
import { lstat, realpath } from "node:fs/promises";
import path from "node:path";
import type {
  ConnectionSetupCapability,
  ConnectionSetupExecute,
  ConnectionSetupItem,
  ConnectionSetupJournal,
  ConnectionSetupPreview,
  ConnectionSetupResult,
  ConnectionSetupSelection,
  SourceResponse,
  SourceSyncState,
} from "@one-memory/contracts";
import {
  type ConnectionClient,
  type DiagnosticSink,
  diagnosticError,
} from "@one-memory/runtime-control";
import axios from "axios";
import type { IpcMainInvokeEvent } from "electron";
import type { ItemJournal } from "../../../packages/runtime-control/src/connection-batch.js";
import type {
  ConnectionController,
  ConnectionControllerTrusted,
} from "../../../packages/runtime-control/src/connection-controller.js";
import type { RecallHostDescriptor } from "./recall-hosts";

interface CoreIdentity {
  port: number;
  apiToken: string;
  instanceId: string;
}
interface TrustedWindow extends Pick<EventEmitter, "on" | "removeListener"> {
  webContents: Pick<EventEmitter, "on" | "removeListener">;
}
interface Options {
  ipc: {
    handle(
      name: string,
      handler: (
        event: IpcMainInvokeEvent,
        ...args: unknown[]
      ) => Promise<unknown>,
    ): unknown;
    removeHandler(name: string): void;
  };
  dataDir: string;
  getCore(): CoreIdentity;
  requireTrustedWindow(event: IpcMainInvokeEvent): TrustedWindow;
  hostPermissions: ConnectionControllerTrusted["hostPermissions"];
  hosts: readonly RecallHostDescriptor[];
  platform?: NodeJS.Platform;
  remote?: ConnectionClient;
  diagnostics?: DiagnosticSink;
  disabledReason?: ConnectionSetupCapability["reason"];
}
const CHANNELS = ["capability", "preview", "execute", "receipt"] as const;
const traceContext = new AsyncLocalStorage<string>();
const SOURCE_HOST: Readonly<Record<string, string>> = {
  cursor: "cursor",
  "claude-code": "claude-code",
  claude: "claude-code",
  codex: "codex",
  workbuddy: "workbuddy",
  opencode: "opencode",
  "qwen-code": "qwen-code",
  hermes: "hermes",
  pi: "pi",
  zcode: "zcode",
};

export function connectionHostId(sourceId: string): string | null {
  return SOURCE_HOST[sourceId] ?? null;
}

/** Registers handlers without touching files or Core. Only explicit actions open the ledger. */
export function installConnectionSetupIpc(options: Options) {
  const remote = options.remote;
  let closing = false;
  let controller: Promise<ConnectionController | null> | null = null;
  let pinned: { path: string; dev: number; ino: number; uid: number } | null =
    null;
  const flights = new Set<Promise<unknown>>();
  const cancellations = new Set<AbortController>();
  const planWindows = new Map<string, TrustedWindow>();
  function core() {
    const value = options.getCore();
    if (
      !Number.isInteger(value.port) ||
      value.port < 1 ||
      value.port > 65535 ||
      !value.apiToken ||
      !value.instanceId
    )
      fail("runtime_unavailable");
    return { ...value };
  }
  async function verifiedRoot() {
    const resolved = await realpath(options.dataDir);
    const original = await lstat(options.dataDir),
      stat = await lstat(resolved);
    if (
      original.isSymbolicLink() ||
      !stat.isDirectory() ||
      (stat.mode & 0o777) !== 0o700 ||
      stat.uid !== process.getuid?.()
    )
      fail("storage_unavailable");
    const value = {
      path: resolved,
      dev: stat.dev,
      ino: stat.ino,
      uid: stat.uid,
    };
    if (pinned && JSON.stringify(value) !== JSON.stringify(pinned))
      fail("storage_unavailable");
    return value;
  }
  function client(identity: CoreIdentity) {
    const traceId = traceContext.getStore();
    return axios.create({
      baseURL: `http://127.0.0.1:${identity.port}`,
      headers: {
        Authorization: `Bearer ${identity.apiToken}`,
        ...(traceId ? { "X-One-Memory-Trace-Id": traceId } : {}),
      },
      timeout: 10000,
      proxy: false,
      maxRedirects: 0,
      maxContentLength: 4 * 1024 * 1024,
      maxBodyLength: 16384,
    });
  }
  async function verifyCore(identity: CoreIdentity) {
    const now = core();
    if (
      now.port !== identity.port ||
      now.apiToken !== identity.apiToken ||
      now.instanceId !== identity.instanceId
    )
      fail("runtime_unavailable");
    const { data } = await client(identity).get<unknown>(
      "/api/v1/runtime/identity",
    );
    const record = object(data);
    if (
      record.status !== "ok" ||
      record.service !== "one-memory-core" ||
      record.instance_id !== identity.instanceId
    )
      fail("runtime_unavailable");
  }
  async function loadController(
    create: boolean,
  ): Promise<ConnectionController | null> {
    if (controller) return controller;
    controller = (async () => {
      const root = await verifiedRoot();
      const identity = core();
      await verifyCore(identity);
      pinned = root;
      const stableOwner = {
        userId: "local",
        runtimeId: createHash("sha256")
          .update(
            JSON.stringify([
              "connection-data-v1",
              root.path,
              root.dev,
              root.ino,
              root.uid,
            ]),
          )
          .digest("hex"),
      };
      const trusted: ConnectionControllerTrusted = {
        dataDir: root.path,
        stableOwner,
        hostPermissions: options.hostPermissions,
        resolveHostLayout(id) {
          const hostId = connectionHostId(id);
          if (!hostId) return null;
          const matches = options.hosts.filter(
            (host) => host.hostId === hostId && host.layout.hostId === hostId,
          );
          return matches.length === 1
            ? structuredClone(matches[0].layout)
            : null;
        },
        async getSources() {
          await verifiedRoot();
          await verifyCore(identity);
          const { data } =
            await client(identity).get<unknown>("/api/v1/sources");
          return sourceMetadata(data);
        },
        async setSourceSync(source, enabled) {
          await verifiedRoot();
          await verifyCore(identity);
          const { data } = await client(identity).post<unknown>(
            `/api/v1/sources/${encodeURIComponent(source.source_id)}/sync-permission`,
            { enabled },
          );
          const response = object(data);
          exact(response, ["source_id", "sync_enabled"]);
          if (
            response.source_id !== source.source_id ||
            response.sync_enabled !== enabled
          )
            fail("invalid_core_metadata");
          return structuredClone(response) as unknown as SourceSyncState;
        },
      };
      const { ConnectionController } = await import(
        "../../../packages/runtime-control/src/connection-controller.js"
      );
      let exists = true;
      try {
        await lstat(path.join(root.path, "connection-batches"));
      } catch (error) {
        if (hasCode(error, "ENOENT")) exists = false;
        else throw error;
      }
      if (!exists && !create) return null;
      return exists
        ? ConnectionController.open(trusted)
        : ConnectionController.initialize(trusted);
    })();
    try {
      const value = await controller;
      if (!value) controller = null;
      return value;
    } catch (error) {
      controller = null;
      throw error;
    }
  }
  async function capability(): Promise<ConnectionSetupCapability> {
    if (closing) return { available: false, reason: "closing" };
    if (options.disabledReason)
      return { available: false, reason: options.disabledReason };
    if (
      (options.platform ?? process.platform) === "win32" ||
      typeof process.getuid !== "function"
    )
      return { available: false, reason: "unsupported_platform" };
    if (remote) {
      try {
        const result = await remote.capability();
        if (result.available !== true) fail("runtime_unavailable");
        return { available: true, reason: null };
      } catch {
        return { available: false, reason: "runtime_unavailable" };
      }
    }
    try {
      await verifiedRoot();
    } catch {
      return { available: false, reason: "storage_unavailable" };
    }
    try {
      await verifyCore(core());
    } catch {
      return { available: false, reason: "runtime_unavailable" };
    }
    try {
      // Existing storage must be readable, but absence never triggers initialization.
      const existing = await loadController(false);
      await existing?.receipt("capability-validation");
    } catch {
      return { available: false, reason: "storage_unavailable" };
    }
    return { available: true, reason: null };
  }
  for (const channel of CHANNELS) {
    options.ipc.handle(
      `one-memory:connection-setup-${channel}`,
      async (event, ...args) => {
        const traceId = randomUUID();
        const started = Date.now();
        let window: TrustedWindow;
        try {
          window = options.requireTrustedWindow(event);
        } catch {
          fail("untrusted_window");
        }
        if (closing) fail("closing");
        if (channel === "capability") {
          if (args.length) fail("invalid_input");
          if (!remote) return capability();
        }
        if (channel !== "capability" && args.length !== 1)
          fail("invalid_input");
        if (
          channel !== "capability" &&
          (options.platform ?? process.platform) === "win32"
        )
          fail("unsupported_platform");
        // Validate before lazy I/O; values are copied before the first await.
        let selection: ConnectionSetupSelection | undefined,
          execution: ConnectionSetupExecute | undefined,
          operationId: string | undefined;
        if (channel === "preview") selection = selectionInput(args[0]);
        if (channel === "execute") execution = executeInput(args[0]);
        if (channel === "receipt") {
          identifier(args[0]);
          operationId = args[0] as string;
        }
        if (execution && planWindows.get(execution.planId) !== window)
          fail("plan_missing");
        const abort = new AbortController();
        cancellations.add(abort);
        const frame = event.senderFrame;
        let contextLost = false;
        const invalidate = () => {
          contextLost = true;
          abort.abort();
        };
        const navigation = () => invalidate();
        window.on("closed", invalidate);
        window.webContents.on("destroyed", invalidate);
        window.webContents.on("did-start-navigation", navigation);
        const stillTrusted = (draining = false) => {
          if (
            contextLost ||
            (!draining && abort.signal.aborted) ||
            event.senderFrame !== frame
          )
            fail("context_changed");
          try {
            if (options.requireTrustedWindow(event) !== window)
              fail("context_changed");
          } catch {
            fail("context_changed");
          }
        };
        const work = traceContext.run(traceId, async () => {
          try {
            await options.diagnostics?.log({
              level: "info",
              component: "desktop.connection",
              event: `connection.${channel}_started`,
              outcome: "started",
              trace_id: traceId,
              ...(operationId ? { operation_id: operationId } : {}),
            });
            if (channel === "capability") {
              stillTrusted();
              const result = await capability();
              stillTrusted();
              return result;
            }
            const instance =
              remote ?? (await loadController(channel !== "receipt"));
            stillTrusted();
            if (selection) {
              if (!instance) fail("storage_unavailable");
              const result = await instance.preview(selection);
              stillTrusted();
              planWindows.set(result.planId, window);
              await options.diagnostics?.log({
                level: "info",
                component: "desktop.connection",
                event: "connection.preview_completed",
                outcome: "succeeded",
                trace_id: traceId,
                operation_id: result.planId,
                duration_ms: Date.now() - started,
                details: { count: result.items.length },
              });
              return structuredClone(result) satisfies ConnectionSetupPreview;
            }
            if (execution) {
              if (!instance) fail("plan_missing");
              // Remote checks abort after discovery, before dispatch. Once sent,
              // drain must wait; lost responses remain recoverable by receipt.
              stillTrusted();
              const result = remote
                ? await remote.execute(execution, abort.signal)
                : await (instance as ConnectionController).execute(
                    execution,
                    abort.signal,
                  );
              if (result.state !== "completed" && result.state !== "partial")
                fail("invalid_controller_result");
              // Drain callers may still receive their receipt; changed renderer contexts may not.
              if (contextLost) fail("context_changed");
              if (remote) stillTrusted(closing);
              else if (!closing) stillTrusted();
              const response = {
                operationId: result.operationId,
                planDigest: result.planDigest,
                state: result.state,
                recovery: result.recovery,
                cancelled: result.cancelled,
                items: result.items.map(publicItem),
                skipped: result.skipped,
              } satisfies ConnectionSetupResult;
              await options.diagnostics?.log({
                level: result.state === "completed" ? "info" : "warn",
                component: "desktop.connection",
                event: "connection.execute_completed",
                outcome: result.state,
                trace_id: traceId,
                operation_id: result.operationId,
                duration_ms: Date.now() - started,
                details: { count: result.items.length },
              });
              return response;
            }
            const journal =
              instance && operationId
                ? await instance.receipt(operationId)
                : null;
            stillTrusted();
            return journal
              ? ({
                  operationId: journal.operationId,
                  planDigest: journal.planDigest,
                  revision: journal.revision,
                  state: journal.items.every((i) => i.state === "succeeded")
                    ? "completed"
                    : "partial",
                  items: journal.items.map(publicItem),
                } satisfies ConnectionSetupJournal)
              : null;
          } catch (error) {
            await options.diagnostics?.log({
              level: "error",
              component: "desktop.connection",
              event: `connection.${channel}_failed`,
              outcome: "failed",
              trace_id: traceId,
              ...(operationId ? { operation_id: operationId } : {}),
              duration_ms: Date.now() - started,
              error_code: "connection_operation_failed",
              ...diagnosticError(error),
            });
            throw safeError(error);
          } finally {
            cancellations.delete(abort);
            window.removeListener("closed", invalidate);
            window.webContents.removeListener("destroyed", invalidate);
            window.webContents.removeListener(
              "did-start-navigation",
              navigation,
            );
          }
        });
        flights.add(work);
        try {
          return await work;
        } finally {
          flights.delete(work);
        }
      },
    );
  }
  return {
    async drain() {
      closing = true;
      for (const abort of cancellations) abort.abort();
      await Promise.allSettled([...flights]);
    },
    dispose() {
      closing = true;
      for (const abort of cancellations) abort.abort();
      for (const channel of CHANNELS)
        options.ipc.removeHandler(`one-memory:connection-setup-${channel}`);
      planWindows.clear();
    },
  };
}

function publicItem(
  item:
    | ItemJournal
    | {
        id: string;
        kind: "sync";
        intentDigest: string;
        state: "succeeded" | "unknown";
        receipt: null;
      },
): ConnectionSetupItem {
  const receipt = item.receipt;
  return {
    id: item.id,
    kind: item.kind,
    state: item.state,
    receipt: !receipt
      ? null
      : {
          kind: receipt.kind,
          hostId: receipt.host.hostId,
          installationId: receipt.host.installationId as string,
          permissionRevision: receipt.host.permissionRevision,
          ...(receipt.kind === "readGrant"
            ? {
                allowedTools: [...receipt.allowedTools],
                allowedScopes: [...receipt.allowedScopes],
              }
            : {}),
        },
  };
}
function selectionInput(input: unknown): ConnectionSetupSelection {
  const value = object(input);
  exact(value, ["syncSourceIds", "hostSourceIds"]);
  for (const field of ["syncSourceIds", "hostSourceIds"]) {
    const values = value[field];
    if (
      !Array.isArray(values) ||
      values.length > 32 ||
      new Set(values).size !== values.length
    )
      fail("invalid_input");
    for (const id of values) {
      sourceId(id);
      if (id === "openclaw") fail("invalid_input");
    }
  }
  if (
    !(value.syncSourceIds as string[]).length &&
    !(value.hostSourceIds as string[]).length
  )
    fail("invalid_input");
  return {
    syncSourceIds: [...(value.syncSourceIds as string[])],
    hostSourceIds: [...(value.hostSourceIds as string[])],
  };
}
function executeInput(input: unknown): ConnectionSetupExecute {
  const value = object(input);
  exact(value, ["planId", "digest", "userConfirmed"]);
  identifier(value.planId);
  if (
    typeof value.digest !== "string" ||
    !/^[a-f0-9]{64}$/.test(value.digest) ||
    value.userConfirmed !== true
  )
    fail("invalid_input");
  return {
    planId: value.planId as string,
    digest: value.digest,
    userConfirmed: true,
  };
}
function sourceMetadata(value: unknown): SourceResponse[] {
  if (!Array.isArray(value) || value.length > 256)
    fail("invalid_core_metadata");
  const seen = new Set<string>();
  return value.map((input) => {
    const s = object(input);
    sourceId(s.source_id);
    if (seen.has(s.source_id as string)) fail("invalid_core_metadata");
    seen.add(s.source_id as string);
    for (const name of ["display_name", "candidate_path"]) text(s[name], 4096);
    for (const name of ["authorized", "available"])
      if (typeof s[name] !== "boolean") fail("invalid_core_metadata");
    if (s.detected !== null && typeof s.detected !== "boolean")
      fail("invalid_core_metadata");
    for (const name of ["grant_revision", "candidate_revision"])
      if (s[name] !== undefined) integer(s[name], 0);
    for (const name of ["imported_messages", "imported_conversations"])
      integer(s[name], 0);
    if (typeof s.sync_enabled !== "boolean") fail("invalid_core_metadata");
    for (const name of ["root_id", "candidate_root_id", "root_label"])
      if (s[name] != null) text(s[name], 4096);
    if (
      !["default", "environment_override", "user_selected"].includes(
        String(s.candidate_kind),
      )
    )
      fail("invalid_core_metadata");
    if (
      s.location_state !== undefined &&
      !["found", "not_found", "inaccessible", "invalid"].includes(
        String(s.location_state),
      )
    )
      fail("invalid_core_metadata");
    if (
      !["full_dynamic", "additions_only", "one_time_snapshot"].includes(
        String(s.sync_completeness),
      )
    )
      fail("invalid_core_metadata");
    const caps = object(s.capabilities);
    exact(caps, [
      "historical_import",
      "incremental_import",
      "live_sync",
      "recall",
      "installer",
      "verification",
    ]);
    for (const key of [
      "historical_import",
      "incremental_import",
      "live_sync",
      "recall",
      "installer",
    ])
      if (!["implemented", "not_implemented"].includes(String(caps[key])))
        fail("invalid_core_metadata");
    if (!["fixture_only", "real_replayed"].includes(String(caps.verification)))
      fail("invalid_core_metadata");
    if (!Array.isArray(s.storage_kinds) || s.storage_kinds.length > 32)
      fail("invalid_core_metadata");
    s.storage_kinds.forEach((k) => {
      text(k, 64);
    });
    // Project only metadata needed by the existing source contract, never arbitrary response keys.
    return {
      source_id: s.source_id,
      display_name: s.display_name,
      candidate_path: s.candidate_path,
      candidate_kind: s.candidate_kind,
      available: s.available,
      authorized: s.authorized,
      detected: s.detected,
      root_label: s.root_label ?? null,
      root_id: s.root_id,
      candidate_root_id: s.candidate_root_id,
      grant_revision: s.grant_revision,
      sync_enabled: s.sync_enabled,
      location_state: s.location_state,
      imported_messages: s.imported_messages,
      imported_conversations: s.imported_conversations,
      storage_kinds: s.storage_kinds,
      capabilities: caps,
      sync_completeness: s.sync_completeness,
    } as unknown as SourceResponse;
  });
}
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    fail("invalid_input");
  return value as Record<string, unknown>;
}
function exact(value: Record<string, unknown>, keys: string[]) {
  if (
    Object.keys(value).length !== keys.length ||
    keys.some((k) => !Object.hasOwn(value, k))
  )
    fail("invalid_input");
}
function sourceId(value: unknown) {
  if (typeof value !== "string" || !/^[a-z0-9][a-z0-9-]{0,127}$/.test(value))
    fail("invalid_input");
}
function identifier(value: unknown) {
  if (
    typeof value !== "string" ||
    !/^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$/.test(value)
  )
    fail("invalid_input");
}
function integer(value: unknown, min: number) {
  if (!Number.isSafeInteger(value) || (value as number) < min)
    fail("invalid_core_metadata");
}
function text(value: unknown, max: number) {
  if (
    typeof value !== "string" ||
    value.length > max ||
    [...value].some(
      (character) =>
        character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127,
    )
  )
    fail("invalid_core_metadata");
}
function hasCode(error: unknown, code: string) {
  return (
    !!error &&
    typeof error === "object" &&
    "code" in error &&
    error.code === code
  );
}
function safeError(error: unknown): Error {
  const message = error instanceof Error ? error.message : "";
  if (
    /^connection_setup_(untrusted_window|closing|invalid_input|context_changed|plan_missing|unsupported_platform|runtime_unavailable|storage_unavailable)$/.test(
      message,
    )
  )
    return Error(message);
  if (message.endsWith("needs_attention"))
    return Error("connection_setup_needs_attention");
  if (message.endsWith("recovery_required"))
    return Error("connection_setup_recovery_required");
  if (message === "connection_controller_plan_missing")
    return Error("connection_setup_plan_missing");
  if (message === "connection_controller_plan_changed")
    return Error("connection_setup_plan_changed");
  return Error("connection_setup_unavailable");
}
function fail(code: string): never {
  throw Error(`connection_setup_${code}`);
}
