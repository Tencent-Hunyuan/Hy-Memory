import path from "node:path";
import { createConnectionClient } from "./connection-client.js";
import type { HostPermissions } from "./host-permissions.js";

type Method =
  | "getPermission"
  | "getSyncHookStatus"
  | "previewSyncHook"
  | "applySyncHook"
  | "setPermission"
  | "revokePermission"
  | "previewSkillSync"
  | "applySkillSync"
  | "previewInstall"
  | "repairInstalledCommand"
  | "install"
  | "previewUninstall"
  | "uninstall";

export type HostPermissionClient = Pick<
  HostPermissions,
  Method | "prepareGenericLocalConnection"
>;

/** Desktop keeps only host IDs; native layouts and permission writes stay in Runtime. */
export function createHostPermissionClient(options: {
  dataDir: string;
}): HostPermissionClient {
  const client = createConnectionClient(options);
  let pending: Promise<unknown> = Promise.resolve();
  function invoke<K extends Method>(
    operation: K,
    input: Parameters<HostPermissions[K]>,
  ) {
    const [layout, ...args] = input;
    const request = structuredClone({ hostId: layout.hostId, operation, args });
    const work = pending.then(async () => {
      if (!client.host) throw Error("connection_client_capability_unavailable");
      try {
        return await client.host(request);
      } catch (error) {
        if (
          isWriteOperation(operation) ||
          !(error instanceof Error) ||
          error.message !== "connection_client_unavailable"
        )
          throw error;
        await new Promise((resolve) => setTimeout(resolve, 100));
        return client.host(request);
      }
    });
    pending = work.catch(() => undefined);
    return work as ReturnType<HostPermissions[K]>;
  }
  return {
    prepareGenericLocalConnection: () => {
      const work = pending.then(async () => {
        if (!client.host)
          throw Error("connection_client_capability_unavailable");
        const result = await client.host({
          hostId: "generic-local-mcp",
          operation: "prepareGenericLocalConnection",
          args: [],
        });
        if (
          !result ||
          typeof result !== "object" ||
          Object.keys(result).length !== 1 ||
          !("credentialFile" in result) ||
          typeof result.credentialFile !== "string" ||
          !path.isAbsolute(result.credentialFile) ||
          path.basename(result.credentialFile) !== "credential" ||
          path.dirname(path.dirname(result.credentialFile)) !==
            path.resolve(
              options.dataDir,
              "host-permissions",
              "installations",
            ) ||
          !/^[a-f0-9]{64}$/.test(
            path.basename(path.dirname(result.credentialFile)),
          )
        )
          throw Error("connection_client_invalid_response");
        return { credentialFile: result.credentialFile };
      });
      pending = work.catch(() => undefined);
      return work;
    },
    getPermission: (...input) => invoke("getPermission", input),
    getSyncHookStatus: (...input) => invoke("getSyncHookStatus", input),
    previewSyncHook: (...input) => invoke("previewSyncHook", input),
    applySyncHook: (...input) => invoke("applySyncHook", input),
    setPermission: (...input) => invoke("setPermission", input),
    revokePermission: (...input) => invoke("revokePermission", input),
    previewSkillSync: (...input) => invoke("previewSkillSync", input),
    applySkillSync: (...input) => invoke("applySkillSync", input),
    previewInstall: (...input) => invoke("previewInstall", input),
    repairInstalledCommand: (...input) =>
      invoke("repairInstalledCommand", input),
    install: (...input) => invoke("install", input),
    previewUninstall: (...input) => invoke("previewUninstall", input),
    uninstall: (...input) => invoke("uninstall", input),
  };
}

function isWriteOperation(operation: Method): boolean {
  return [
    "applySyncHook",
    "setPermission",
    "revokePermission",
    "applySkillSync",
    "repairInstalledCommand",
    "install",
    "uninstall",
  ].includes(operation);
}
