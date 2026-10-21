import { createHash } from "node:crypto";
import { lstat, realpath } from "node:fs/promises";
import path from "node:path";
import type { HostPairingReceipt } from "@one-memory/host-bridge";
import { isConnectionControlError } from "@one-memory/host-bridge";
import axios from "axios";
import { inspectRecallBrokerStatus } from "./broker-status.js";
import type { BatchJournal, BatchResult } from "./connection-batch.js";
import type {
  ConnectionControllerPreview,
  ConnectionSelection,
} from "./connection-controller.js";
import { processIsAlive } from "./core-supervisor.js";
import { discoverRuntimeAttachment } from "./discovery.js";

export type ConnectionClientResult = BatchResult & {
  skipped: Array<{
    id: string;
    state: "unchanged" | "changed" | "unavailable";
  }>;
};
export interface ConnectionClient {
  capability(): Promise<{ available: true }>;
  preview(input: ConnectionSelection): Promise<ConnectionControllerPreview>;
  execute(
    input: {
      planId: string;
      digest: string;
      userConfirmed: true;
    },
    signal?: AbortSignal,
  ): Promise<ConnectionClientResult>;
  receipt(operationId: string): Promise<BatchJournal | null>;
  createPairing?(displayName: string): Promise<HostPairingReceipt>;
  host?(input: {
    hostId: string;
    operation: string;
    args: unknown[];
  }): Promise<unknown>;
}
type Action =
  | "capability"
  | "preview"
  | "execute"
  | "receipt"
  | "host"
  | "pairing";
type Root = { path: string; dev: number; ino: number; uid: number };

/** Trusted local CLI control client, not a renderer API. Never initializes a
 * controller, grant store or ledger. Layouts/resources/approval stay in Runtime.
 * An execute is dispatched at most once per preview in this client. A caller
 * must persist operationId before execute and use receipt after process loss.
 */
export function createConnectionClient(options: {
  dataDir: string;
}): ConnectionClient {
  exact(options, ["dataDir"], "invalid_input");
  if (
    typeof options.dataDir !== "string" ||
    !path.isAbsolute(options.dataDir) ||
    options.dataDir.includes("\0")
  )
    fail("invalid_input");
  const dataDir = options.dataDir;
  let pinned: Root | undefined;
  const plans = new Map<string, ConnectionControllerPreview>();
  const attempted = new Set<string>();
  let busy = false;

  async function root(): Promise<Root> {
    try {
      if (process.platform === "win32" || !process.getuid)
        fail("storage_unavailable");
      const resolved = await realpath(dataDir);
      const original = await lstat(dataDir),
        info = await lstat(resolved);
      if (
        original.isSymbolicLink() ||
        !info.isDirectory() ||
        (info.mode & 0o777) !== 0o700 ||
        info.uid !== process.getuid()
      )
        fail("storage_unavailable");
      const current = {
        path: resolved,
        dev: info.dev,
        ino: info.ino,
        uid: info.uid,
      };
      if (pinned && JSON.stringify(pinned) !== JSON.stringify(current))
        fail("storage_unavailable");
      pinned = current;
      return current;
    } catch {
      return fail("storage_unavailable");
    }
  }
  async function verify() {
    const directory = await root();
    try {
      const runtime = await discoverRuntimeAttachment({
        dataDir: directory.path,
        processIsAlive,
        requireServiceReceipt: true,
        async probeIdentity(port, apiToken) {
          return (
            await axios.get(
              `http://127.0.0.1:${port}/api/v1/runtime/identity`,
              {
                headers: { Authorization: `Bearer ${apiToken}` },
                timeout: 30000,
                proxy: false,
                maxRedirects: 0,
                maxContentLength: 16384,
              },
            )
          ).data;
        },
      });
      if (
        !runtime ||
        !Number.isSafeInteger(runtime.servicePid) ||
        (runtime.servicePid ?? 0) <= 1
      )
        fail("runtime_unavailable");
      const broker = await inspectRecallBrokerStatus({
        dataDir: directory.path,
        expectedInstanceId: runtime.instanceId,
        async probeBroker(discovery) {
          if (discovery.pid !== runtime.servicePid) fail("runtime_unavailable");
          return (
            await axios.get<unknown>(
              `http://127.0.0.1:${discovery.port}/health`,
              {
                timeout: 30000,
                proxy: false,
                maxRedirects: 0,
                maxContentLength: 16384,
              },
            )
          ).data;
        },
      });
      if (
        broker.state !== "running" ||
        broker.instanceId !== runtime.instanceId ||
        broker.host !== "127.0.0.1" ||
        !Number.isSafeInteger(broker.port) ||
        (broker.port ?? 0) < 1 ||
        (broker.port ?? 0) > 65535
      )
        fail("runtime_unavailable");
      await root();
      return {
        runtime,
        port: broker.port,
        owner: createHash("sha256")
          .update(
            JSON.stringify([
              "connection-data-v1",
              directory.path,
              directory.dev,
              directory.ino,
              directory.uid,
            ]),
          )
          .digest("hex"),
      };
    } catch (error) {
      if (
        error instanceof Error &&
        error.message === "connection_client_storage_unavailable"
      )
        throw error;
      return fail("runtime_unavailable");
    }
  }
  async function call(
    action: Action,
    input: object,
    dispatched?: () => void,
    signal?: AbortSignal,
  ) {
    if (busy) fail("in_flight");
    busy = true;
    let sent = false;
    try {
      const before = await verify();
      if (signal?.aborted) fail("cancelled");
      dispatched?.();
      sent = true;
      let response: { status: number; data: unknown };
      try {
        response = await axios.request<unknown>({
          method: "POST",
          url: `http://127.0.0.1:${before.port}/control/connections`,
          headers: {
            Authorization: `Bearer ${before.runtime.apiToken}`,
            "Content-Type": "application/json",
          },
          data: {
            schema_version: 1,
            instance_id: before.runtime.instanceId,
            action,
            input,
          },
          timeout: 30000,
          maxBodyLength: 65536,
          maxContentLength: 1048576,
          proxy: false,
          maxRedirects: 0,
        });
      } catch (error) {
        const failure = record(record(error)?.response)?.data;
        const code = record(failure)?.error;
        if (
          action !== "execute" &&
          record(failure) &&
          Object.keys(failure as object).length === 1 &&
          isConnectionControlError(code)
        )
          throw Error(code);
        if (
          action === "capability" &&
          axios.isAxiosError(error) &&
          error.response?.status === 404
        )
          fail("capability_unavailable");
        // Tests and non-Axios transports may expose only a status, never error text.
        if (
          action === "capability" &&
          record(error)?.response &&
          record(record(error)?.response)?.status === 404
        )
          fail("capability_unavailable");
        fail("unavailable");
      }
      const after = await verify();
      if (
        before.runtime.instanceId !== after.runtime.instanceId ||
        before.runtime.apiToken !== after.runtime.apiToken ||
        before.runtime.port !== after.runtime.port ||
        before.runtime.servicePid !== after.runtime.servicePid ||
        before.port !== after.port
      )
        fail("runtime_unavailable");
      const envelope = exact(response.data, [
        "schema_version",
        "instance_id",
        "action",
        "result",
      ]);
      if (
        response.status !== 200 ||
        envelope.schema_version !== 1 ||
        envelope.instance_id !== before.runtime.instanceId ||
        envelope.action !== action
      )
        fail("invalid_response");
      return { value: envelope.result, owner: before.owner };
    } catch (error) {
      if (action === "execute" && sent) fail("operation_unknown");
      if (error instanceof Error && isConnectionControlError(error.message))
        throw error;
      if (
        error instanceof Error &&
        /^connection_client_(storage_unavailable|runtime_unavailable|invalid_response|capability_unavailable|in_flight|unavailable|cancelled)$/.test(
          error.message,
        )
      )
        throw error;
      return fail("unavailable");
    } finally {
      busy = false;
    }
  }
  return {
    async createPairing(displayName) {
      if (
        typeof displayName !== "string" ||
        displayName !== displayName.trim() ||
        displayName.length < 1 ||
        displayName.length > 80 ||
        /[\0\r\n]/.test(displayName)
      )
        fail("invalid_input");
      const { value } = await call("pairing", { displayName });
      const result = exact(value, [
        "hostId",
        "displayName",
        "pairingCode",
        "expiresAt",
      ]);
      if (
        typeof result.hostId !== "string" ||
        typeof result.displayName !== "string" ||
        typeof result.pairingCode !== "string" ||
        !/^\d{6}$/.test(result.pairingCode) ||
        typeof result.expiresAt !== "string" ||
        !Number.isFinite(Date.parse(result.expiresAt))
      )
        fail("invalid_response");
      return result as unknown as HostPairingReceipt;
    },
    async host(input) {
      exact(input, ["hostId", "operation", "args"], "invalid_input");
      if (
        !isId(input.hostId) ||
        input.hostId === "openclaw" ||
        typeof input.operation !== "string" ||
        !Array.isArray(input.args) ||
        input.args.length > 2
      )
        fail("invalid_input");
      return (await call("host", structuredClone(input))).value;
    },
    async capability() {
      const { value } = await call("capability", {});
      const result = exact(value, ["available"]);
      if (result.available !== true) fail("capability_unavailable");
      return { available: true };
    },
    async preview(input) {
      const value = exact(
        input,
        ["syncSourceIds", "hostSourceIds"],
        "invalid_input",
      );
      for (const key of ["syncSourceIds", "hostSourceIds"]) {
        const list = value[key];
        if (
          !Array.isArray(list) ||
          list.length > 32 ||
          new Set(list).size !== list.length ||
          list.some(
            (id) =>
              typeof id !== "string" ||
              !/^[a-z0-9][a-z0-9-]{0,127}$/.test(id) ||
              id === "openclaw",
          )
        )
          fail("invalid_input");
      }
      if (!input.syncSourceIds.length && !input.hostSourceIds.length)
        fail("invalid_input");
      const { value: result } = await call("preview", structuredClone(input));
      validatePreview(result);
      if (plans.has(result.planId)) fail("invalid_response");
      if (plans.size >= 64) {
        const oldest = plans.keys().next().value;
        if (oldest !== undefined) {
          plans.delete(oldest);
          attempted.delete(oldest);
        }
      }
      plans.set(result.planId, structuredClone(result));
      return result;
    },
    async execute(input, signal) {
      exact(input, ["planId", "digest", "userConfirmed"], "invalid_input");
      if (
        typeof input.planId !== "string" ||
        !isHash(input.digest) ||
        input.userConfirmed !== true
      )
        fail("invalid_input");
      if (attempted.has(input.planId)) fail("operation_unknown");
      const plan = plans.get(input.planId);
      if (!plan || plan.digest !== input.digest) fail("plan_missing");
      const { value, owner } = await call(
        "execute",
        structuredClone(input),
        () => attempted.add(input.planId),
        signal,
      );
      try {
        validateResult(value, owner, plan.operationId);
        if (value.planDigest !== plan.digest) fail("invalid_response");
        const returned = [...value.items, ...value.skipped];
        if (
          returned.length !== plan.items.length ||
          new Set(returned.map((item) => item.id)).size !== returned.length ||
          returned.some(
            (item) => !plan.items.some((expected) => expected.id === item.id),
          )
        )
          fail("invalid_response");
        for (const item of value.items) {
          if (
            !plan.items.some(
              (expected) =>
                expected.id === item.id && expected.kind === item.kind,
            )
          )
            fail("invalid_response");
        }
        return value;
      } catch {
        return fail("operation_unknown");
      }
    },
    async receipt(operationId) {
      if (!isId(operationId)) fail("invalid_input");
      const { value, owner } = await call("receipt", { operationId });
      if (value === null) return null;
      validateJournal(value, owner, operationId);
      return value;
    },
  };
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}
function exact(value: unknown, keys: string[], code = "invalid_response") {
  const object = record(value);
  if (
    !object ||
    Object.keys(object).length !== keys.length ||
    keys.some((key) => !Object.hasOwn(object, key))
  )
    fail(code);
  return object;
}
function isId(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$/.test(value)
  );
}
function isHash(value: unknown): value is string {
  return typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
}
function integer(value: unknown, minimum = 0) {
  if (!Number.isSafeInteger(value) || (value as number) < minimum)
    fail("invalid_response");
}
function strings(value: unknown, maximum = 128): asserts value is string[] {
  if (
    !Array.isArray(value) ||
    value.length > maximum ||
    new Set(value).size !== value.length ||
    value.some(
      (item) =>
        typeof item !== "string" ||
        !item.length ||
        item.length > 512 ||
        [...item].some(
          (character) =>
            character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127,
        ),
    )
  )
    fail("invalid_response");
}
function rights(tools: unknown, scopes: unknown) {
  strings(tools, 5);
  strings(scopes, 64);
  if (
    tools.some(
      (tool) =>
        ![
          "om_recall",
          "om_ask",
          "om_conversations",
          "om_matters",
          "om_diagnostic_trace",
        ].includes(tool),
    ) ||
    scopes.some(
      (scope) =>
        scope !== "all-apps" &&
        !/^workspace:v1:[a-f0-9]{64}$/.test(scope) &&
        !/^source:[a-z0-9][a-z0-9-]{0,127}$/.test(scope),
    )
  )
    fail("invalid_response");
}
function validatePreview(
  value: unknown,
): asserts value is ConnectionControllerPreview {
  const v = exact(value, ["planId", "operationId", "digest", "items"]);
  if (
    !isId(v.planId) ||
    !isId(v.operationId) ||
    !isHash(v.digest) ||
    !Array.isArray(v.items) ||
    v.items.length > 96
  )
    fail("invalid_response");
  const ids = new Set<string>();
  for (const item of v.items) {
    const row = record(item);
    if (
      !row ||
      !isId(row.id) ||
      ids.has(row.id) ||
      !["sync", "install", "readGrant"].includes(String(row.kind)) ||
      typeof row.restoresRevoked !== "boolean"
    )
      fail("invalid_response");
    ids.add(row.id);
    const base = ["id", "kind", "sourceIds", "restoresRevoked"];
    exact(row, [
      ...base,
      ...(row.kind !== "sync" ? ["hostId"] : []),
      ...(row.kind === "readGrant"
        ? [
            "allowedTools",
            "allowedScopes",
            "action",
            "previousTools",
            "previousScopes",
          ]
        : []),
    ]);
    strings(row.sourceIds, 32);
    if (row.sourceIds.some((id) => !/^[a-z0-9][a-z0-9-]{0,127}$/.test(id)))
      fail("invalid_response");
    if (row.kind !== "sync" && !isId(row.hostId)) fail("invalid_response");
    if (row.kind === "readGrant") {
      rights(row.allowedTools, row.allowedScopes);
      rights(row.previousTools, row.previousScopes);
      if (!["unchanged", "add-rights", "grant"].includes(String(row.action)))
        fail("invalid_response");
    }
  }
}
function items(value: unknown, owner: string, operationId: string) {
  if (!Array.isArray(value) || value.length > 96) fail("invalid_response");
  const seen = new Set<string>();
  for (const candidate of value) {
    const row = exact(candidate, [
      "id",
      "kind",
      "intentDigest",
      "state",
      "receipt",
    ]);
    if (
      !isId(row.id) ||
      seen.has(row.id) ||
      !isHash(row.intentDigest) ||
      !["sync", "install", "readGrant"].includes(String(row.kind)) ||
      ![
        "pending",
        "in_flight",
        "succeeded",
        "unknown",
        "rejected",
        "changed",
        "not_applied",
        "unavailable",
        "dependency_blocked",
      ].includes(String(row.state))
    )
      fail("invalid_response");
    seen.add(row.id);
    if (row.kind === "sync") {
      if (row.receipt !== null) fail("invalid_response");
      continue;
    }
    if (row.state !== "succeeded") {
      if (row.receipt !== null) fail("invalid_response");
      continue;
    }
    const keys = [
      "userId",
      "runtimeId",
      "operationId",
      "itemId",
      "intentDigest",
      "kind",
    ];
    const r = exact(row.receipt, [
      ...keys,
      "host",
      ...(row.kind === "readGrant" ? ["allowedTools", "allowedScopes"] : []),
    ]);
    if (
      r.userId !== "local" ||
      r.runtimeId !== owner ||
      r.operationId !== operationId ||
      r.itemId !== row.id ||
      r.intentDigest !== row.intentDigest ||
      r.kind !== row.kind
    )
      fail("invalid_response");
    const h = exact(r.host, [
      "hostId",
      "layoutDigest",
      "installationId",
      "permissionRevision",
      "installationDigest",
    ]);
    if (
      !isId(h.hostId) ||
      !isHash(h.layoutDigest) ||
      !isId(h.installationId) ||
      !isHash(h.installationDigest)
    )
      fail("invalid_response");
    integer(h.permissionRevision, 1);
    if (row.kind === "readGrant") rights(r.allowedTools, r.allowedScopes);
  }
}
function validateJournal(
  value: unknown,
  owner: string,
  operationId: string,
): asserts value is BatchJournal {
  const v = exact(value, [
    "schemaVersion",
    "userId",
    "runtimeId",
    "operationId",
    "planDigest",
    "revision",
    "items",
  ]);
  if (
    v.schemaVersion !== 1 ||
    v.userId !== "local" ||
    v.runtimeId !== owner ||
    v.operationId !== operationId ||
    !isHash(v.planDigest)
  )
    fail("invalid_response");
  integer(v.revision, 1);
  items(v.items, owner, operationId);
}
function validateResult(
  value: unknown,
  owner: string,
  operationId: string,
): asserts value is ConnectionClientResult {
  const v = exact(value, [
    "mode",
    "operationId",
    "planDigest",
    "state",
    "recovery",
    "cancelled",
    "items",
    "skipped",
  ]);
  if (
    v.mode !== "execute" ||
    v.operationId !== operationId ||
    !isHash(v.planDigest) ||
    !["completed", "partial"].includes(String(v.state)) ||
    !["none", "process", "persistent"].includes(String(v.recovery)) ||
    typeof v.cancelled !== "boolean"
  )
    fail("invalid_response");
  items(v.items, owner, operationId);
  if (!Array.isArray(v.skipped) || v.skipped.length > 96)
    fail("invalid_response");
  for (const entry of v.skipped) {
    const s = exact(entry, ["id", "state"]);
    if (
      !isId(s.id) ||
      !["unchanged", "changed", "unavailable"].includes(String(s.state))
    )
      fail("invalid_response");
  }
  if (
    v.state === "completed" &&
    ((v.items as Array<{ state: string }>).some(
      (item) => item.state !== "succeeded",
    ) ||
      v.skipped.some((item) => item.state !== "unchanged") ||
      v.cancelled)
  )
    fail("invalid_response");
}
function fail(code: string): never {
  throw new Error(`connection_client_${code}`);
}
