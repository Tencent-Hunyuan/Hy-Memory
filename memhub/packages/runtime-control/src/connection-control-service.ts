import { createHash } from "node:crypto";
import { lstat, realpath } from "node:fs/promises";
import path from "node:path";
import type { SourceResponse, SourceSyncState } from "@one-memory/contracts";
import type { ConnectionControlRequest } from "@one-memory/host-bridge";
import type { RecallHostLayout } from "@one-memory/host-bridge/installer";
import axios from "axios";
import type {
  ConnectionController,
  ConnectionSelection,
} from "./connection-controller.js";
import { connectionSourceMetadata } from "./connection-source-metadata.js";
import { createHostControlService } from "./host-control-service.js";
import type { HostPermissions } from "./host-permissions.js";
import type { RecallHostDescriptor } from "./recall-hosts.js";
import type { RuntimeConnection } from "./runtime-supervisor.js";

interface Options {
  dataDir: string;
  connection: RuntimeConnection;
  permissions: HostPermissions;
  layouts: ReadonlyMap<string, RecallHostLayout>;
  hosts?: readonly RecallHostDescriptor[];
}

/** Constructed inside the Runtime with its existing permission owner. */
export function createConnectionControlService(options: Options) {
  const hostControl = createHostControlService({
    permissions: options.permissions,
    hosts: options.hosts ?? [],
  });
  let controller: Promise<ConnectionController | null> | null = null;
  let pinned: string | null = null;
  let stopped = false;
  let inFlight: Promise<unknown> | null = null;
  let queued = 0;
  const client = axios.create({
    baseURL: options.connection.coreBaseUrl,
    headers: { Authorization: `Bearer ${options.connection.coreApiToken}` },
    proxy: false,
    maxRedirects: 0,
    timeout: 10000,
    maxContentLength: 4 * 1024 * 1024,
    maxBodyLength: 16384,
  });
  async function verify() {
    if (stopped || process.platform === "win32" || !process.getuid) fail();
    const resolved = await realpath(options.dataDir);
    const original = await lstat(options.dataDir);
    const info = await lstat(resolved);
    if (
      original.isSymbolicLink() ||
      !info.isDirectory() ||
      (info.mode & 0o777) !== 0o700 ||
      info.uid !== process.getuid()
    )
      fail();
    const identity = JSON.stringify([
      "connection-data-v1",
      resolved,
      info.dev,
      info.ino,
      info.uid,
    ]);
    if (pinned !== null && pinned !== identity) fail();
    pinned = identity;
    const { data } = await client.get("/api/v1/runtime/identity");
    if (
      data?.status !== "ok" ||
      data.service !== "one-memory-core" ||
      data.instance_id !== options.connection.instanceId ||
      stopped
    )
      fail();
    return {
      path: resolved,
      runtimeId: createHash("sha256").update(identity).digest("hex"),
    };
  }
  async function load(create: boolean) {
    if (controller) return controller;
    controller = (async () => {
      const root = await verify();
      let exists = true;
      try {
        await lstat(path.join(root.path, "connection-batches"));
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === "ENOENT") exists = false;
        else throw error;
      }
      if (!exists && !create) return null;
      const { ConnectionController } = await import(
        "./connection-controller.js"
      );
      const trusted = {
        dataDir: root.path,
        stableOwner: { userId: "local", runtimeId: root.runtimeId },
        hostPermissions: options.permissions,
        resolveHostLayout(id: string) {
          return options.layouts.get(id) ?? null;
        },
        async getSources() {
          await verify();
          const { data } = await client.get<unknown>("/api/v1/sources");
          const sources = connectionSourceMetadata(data);
          await verify();
          return sources;
        },
        async setSourceSync(source: SourceResponse, enabled: boolean) {
          await verify();
          const { data } = await client.post<SourceSyncState>(
            `/api/v1/sources/${encodeURIComponent(source.source_id)}/sync-permission`,
            { enabled },
          );
          await verify();
          if (
            data.source_id !== source.source_id ||
            data.sync_enabled !== enabled
          )
            fail();
          return structuredClone(data);
        },
      };
      return exists
        ? ConnectionController.open(trusted)
        : ConnectionController.initialize(trusted);
    })();
    try {
      const result = await controller;
      if (!result) controller = null;
      return result;
    } catch (error) {
      controller = null;
      throw error;
    }
  }
  async function dispatch(request: ConnectionControlRequest) {
    if (request.instance_id !== options.connection.instanceId) fail();
    await verify();
    let result: unknown;
    if (request.action === "capability") {
      if (Object.keys(request.input).length) fail();
      result = { available: true };
    } else if (request.action === "pairing") {
      if (
        Object.keys(request.input).length !== 1 ||
        typeof request.input.displayName !== "string"
      )
        fail();
      result = await options.permissions.createCustomPairing(
        request.input.displayName,
      );
    } else if (request.action === "host") {
      result = await hostControl.handle(request.input);
    } else if (request.action === "receipt") {
      if (
        Object.keys(request.input).length !== 1 ||
        typeof request.input.operationId !== "string" ||
        !/^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$/.test(request.input.operationId)
      )
        fail();
      result =
        (await (await load(false))?.receipt(request.input.operationId)) ?? null;
    } else if (request.action === "preview") {
      const value = await load(true);
      if (!value) fail();
      result = await value.preview(
        request.input as unknown as ConnectionSelection,
      );
    } else if (request.action === "execute") {
      const value = await load(false);
      if (!value) fail();
      result = await value.execute(
        request.input as unknown as {
          planId: string;
          digest: string;
          userConfirmed: true;
        },
      );
    } else fail();
    await verify();
    return result;
  }
  return {
    handle(request: ConnectionControlRequest) {
      if (stopped || queued >= 32)
        return Promise.reject(Error("connection_control_busy"));
      const input = structuredClone(request);
      queued++;
      const work = (inFlight ?? Promise.resolve())
        .catch(() => {})
        .then(() => dispatch(input));
      const finished = work.finally(() => {
        queued--;
        if (inFlight === finished) inFlight = null;
      });
      inFlight = finished;
      return finished;
    },
    async stop() {
      stopped = true;
      await inFlight?.catch(() => {});
    },
  };
}
function fail(): never {
  throw Error("connection_control_unavailable");
}
