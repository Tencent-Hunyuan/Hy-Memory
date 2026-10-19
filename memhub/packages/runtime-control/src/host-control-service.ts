import {
  checkSkillDrift,
  planRecallRepoint,
  readInstallSelfCheck,
  verifyRecallInstall,
} from "@one-memory/host-bridge/installer";
import {
  type HostPermissions,
  LOCAL_EXPERIENCE_UNAVAILABLE,
} from "./host-permissions.js";
import { isHostPresent, type RecallHostDescriptor } from "./recall-hosts.js";
import { syncHookInput } from "./sync-hook.js";

const ARITY = {
  prepareGenericLocalConnection: 0,
  getPermission: 0,
  getSyncHookStatus: 0,
  previewSyncHook: 1,
  applySyncHook: 1,
  setPermission: 1,
  revokePermission: 1,
  previewSkillSync: 0,
  applySkillSync: 2,
  previewInstall: 0,
  repairInstalledCommand: 0,
  install: 1,
  previewUninstall: 0,
  uninstall: 1,
  inspect: 0,
  probe: 0,
} as const;
export type HostControlOperation = keyof typeof ARITY;

function fail(): never {
  throw Error("host_control_invalid_input");
}
function exact(
  value: unknown,
  keys: readonly string[],
): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail();
  const own = Reflect.ownKeys(value);
  if (
    own.length !== keys.length ||
    own.some((key) => typeof key !== "string" || !keys.includes(key))
  )
    fail();
  const result: Record<string, unknown> = {};
  for (const key of keys) {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (!descriptor || !("value" in descriptor)) fail();
    result[key] = descriptor.value;
  }
  return result;
}
function fingerprint(value: unknown) {
  if (typeof value !== "string" || !/^[a-f0-9]{64}$/.test(value)) fail();
}
function strings(value: unknown, maximum: number): asserts value is string[] {
  if (!Array.isArray(value) || value.length > maximum) fail();
  for (let index = 0; index < value.length; index++) {
    const descriptor = Object.getOwnPropertyDescriptor(value, String(index));
    if (
      !descriptor ||
      !("value" in descriptor) ||
      typeof descriptor.value !== "string"
    )
      fail();
  }
  if (
    Reflect.ownKeys(value).length !== value.length + 1 ||
    new Set(value).size !== value.length
  )
    fail();
}
function validateArgs(operation: HostControlOperation, args: unknown[]): void {
  if (
    operation === "install" ||
    operation === "uninstall" ||
    operation === "applySkillSync"
  ) {
    fingerprint(args[0]);
    if (operation === "applySkillSync" && typeof args[1] !== "boolean") fail();
  } else if (operation === "previewSyncHook") {
    if (args[0] !== "install" && args[0] !== "uninstall") fail();
  } else if (operation === "applySyncHook") {
    const input = exact(args[0], [
      "operation",
      "installationId",
      "revision",
      "fingerprint",
      "userConfirmed",
    ]);
    try {
      syncHookInput(input);
    } catch {
      fail();
    }
  } else if (
    operation === "setPermission" ||
    operation === "revokePermission"
  ) {
    const input = exact(args[0], [
      "installationId",
      "expectedRevision",
      "userConfirmed",
      ...(operation === "setPermission"
        ? ["allowedTools", "allowedScopes"]
        : []),
    ]);
    if (
      typeof input.installationId !== "string" ||
      !/^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$/.test(input.installationId) ||
      !Number.isSafeInteger(input.expectedRevision) ||
      (input.expectedRevision as number) < 1 ||
      input.userConfirmed !== true
    )
      fail();
    if (operation === "setPermission") {
      strings(input.allowedTools, 5);
      strings(input.allowedScopes, 128);
      if (
        !input.allowedTools.length ||
        !input.allowedScopes.length ||
        input.allowedTools.some(
          (tool) =>
            ![
              "om_recall",
              "om_ask",
              "om_conversations",
              "om_matters",
              "om_diagnostic_trace",
            ].includes(tool),
        ) ||
        input.allowedScopes.some(
          (scope) =>
            scope !== "all-apps" &&
            !/^workspace:v1:[a-f0-9]{64}$/.test(scope) &&
            !/^source:[a-z0-9][a-z0-9-]{0,127}$/.test(scope),
        )
      )
        fail();
    }
  }
}

/** Runs inside the existing Runtime service flight and uses its permission owner.
 * Callers supply identities and confirmations, never host layouts or credentials.
 */
export function createHostControlService(options: {
  permissions: HostPermissions;
  hosts: readonly RecallHostDescriptor[];
}) {
  const permissions = options.permissions;
  const hosts = new Map<string, RecallHostDescriptor>();
  for (const source of options.hosts) {
    if (source.hostId === "openclaw") continue;
    if (source.hostId === "generic-local-mcp") fail();
    if (source.hostId !== source.layout.hostId || hosts.has(source.hostId))
      fail();
    hosts.set(source.hostId, structuredClone(source));
  }
  return {
    async handle(input: unknown): Promise<unknown> {
      const request = exact(input, ["hostId", "operation", "args"]);
      if (
        typeof request.hostId !== "string" ||
        request.hostId === "openclaw" ||
        typeof request.operation !== "string" ||
        !Object.hasOwn(ARITY, request.operation) ||
        !Array.isArray(request.args)
      )
        fail();
      const generic = request.hostId === "generic-local-mcp";
      const operation = request.operation as HostControlOperation;
      if (generic !== (operation === "prepareGenericLocalConnection")) fail();
      const host = generic ? null : hosts.get(request.hostId);
      if (!generic && !host) fail();
      if (
        request.args.length !== ARITY[operation] ||
        Reflect.ownKeys(request.args).length !== request.args.length + 1
      )
        fail();
      const raw: unknown[] = [];
      for (let index = 0; index < request.args.length; index++) {
        const descriptor = Object.getOwnPropertyDescriptor(
          request.args,
          String(index),
        );
        if (!descriptor || !("value" in descriptor)) fail();
        raw.push(descriptor.value);
      }
      validateArgs(operation, raw);
      let args: unknown[];
      try {
        args = structuredClone(raw);
      } catch {
        return fail();
      }
      try {
        if (generic) return await permissions.prepareGenericLocalConnection();
        if (!host) fail();
        const layout = structuredClone(host.layout);
        switch (operation) {
          case "prepareGenericLocalConnection":
            return fail();
          case "getPermission":
            return await permissions.getPermission(layout);
          case "getSyncHookStatus":
            return await permissions.getSyncHookStatus(layout);
          case "previewSyncHook":
            return await permissions.previewSyncHook(layout, args[0]);
          case "applySyncHook":
            return await permissions.applySyncHook(layout, args[0]);
          case "setPermission":
            return await permissions.setPermission(layout, args[0]);
          case "revokePermission":
            return await permissions.revokePermission(layout, args[0]);
          case "previewSkillSync":
            return await permissions.previewSkillSync(layout);
          case "applySkillSync":
            return await permissions.applySkillSync(layout, args[0], args[1]);
          case "previewInstall":
            return await permissions.previewInstall(layout);
          case "repairInstalledCommand":
            return await permissions.repairInstalledCommand(layout);
          case "install":
            return await permissions.install(layout, args[0]);
          case "previewUninstall":
            return await permissions.previewUninstall(layout);
          case "uninstall":
            return await permissions.uninstall(layout, args[0]);
          case "inspect": {
            const permission = await permissions.getPermission(layout);
            let installed = false;
            try {
              await verifyRecallInstall(layout);
              installed = true;
            } catch {
              /* Existing list semantics. */
            }
            return {
              hostId: host.hostId,
              label: host.label,
              present: await isHostPresent(host),
              installed,
              permission,
              selfCheck: await readInstallSelfCheck(layout),
              skillDrift: installed
                ? await checkSkillDrift(layout, permissions.runtimeFor(layout))
                : null,
            };
          }
          case "probe":
            return await permissions.withHostLock(layout, async () => {
              const runtime = permissions.runtimeFor(layout);
              const health = await permissions.recordLinkCheck(layout);
              const repoint = await planRecallRepoint(layout, runtime);
              return {
                hostId: host.hostId,
                probe: health.link,
                skill: health.skill,
                repoint:
                  repoint === null
                    ? null
                    : { from: repoint.from, to: repoint.to },
              };
            });
        }
      } catch (error) {
        // Preserve actionable fixed codes, never transport errors or arbitrary native text.
        const safe = [
          "sync_hook_remove_first",
          "sync_hook_preview_stale",
          "sync_hook_apply_failed",
          "sync_hook_connection_changed",
          "sync_hook_connection_required",
          "sync_hook_unsupported",
          "host_install_preview_stale",
          "host_install_failed",
          "host_install_modified_files",
          "host_uninstall_preview_stale",
          "host_uninstall_modified_files",
          "host_uninstall_needs_review",
          "host_permission_relink_required",
          "host_permission_installation_incomplete",
          "host_permission_unavailable",
          "host_permission_storage_unsafe",
          "host_permission_transport_unsupported",
          "host_runtime_current_unavailable",
          "host_runtime_app_location_unsafe",
          "host_runtime_current_unsafe",
          "host_runtime_current_changed",
          "host_runtime_identity_mismatch",
          "host_runtime_component_mismatch",
          LOCAL_EXPERIENCE_UNAVAILABLE,
        ];
        throw Error(
          error instanceof Error && safe.includes(error.message)
            ? error.message
            : "host_control_unavailable",
        );
      }
    },
  };
}
