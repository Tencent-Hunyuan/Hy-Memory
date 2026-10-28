#!/usr/bin/env node

import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { access, lstat, readlink, realpath } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { RecallBroker } from "@one-memory/host-bridge";
import {
  type BackgroundHealth,
  inspectBackgroundHealth,
} from "./background-health.js";
import { createConnectionControlService } from "./connection-control-service.js";
import { CoreSupervisor } from "./core-supervisor.js";
import { DiagnosticLogger } from "./diagnostic-log.js";
import { HostPermissions } from "./host-permissions.js";
import { verifyInstalledDistribution } from "./local-install.js";
import { RecallBrokerSupervisor } from "./recall-broker-supervisor.js";
import { recallHosts } from "./recall-hosts.js";
import { RuntimeService } from "./runtime-service.js";
import { RuntimeSupervisor } from "./runtime-supervisor.js";
import {
  runSdkMigration,
  type SdkMigrationOptions,
  type SdkMigrationResult,
} from "./sdk-migration.js";
import {
  readSdkMigrationBlock,
  writeSdkMigrationLog,
} from "./sdk-migration-log.js";
import { createServiceLogCapture } from "./service-log.js";
import {
  appOwnedRuntimeShouldStop,
  disableRuntimeService,
  readRuntimeServiceOwner,
} from "./service-ownership.js";
import {
  defaultManagedInstallRoot,
  hourlyIsolationFromEnvironment,
  hourlyManagedDataDirFromEnvironment,
  planUserService,
  type RuntimePlatform,
  runtimeInstallLayout,
} from "./service-plan.js";
import { createSourceChangeHandler } from "./source-change-handler.js";

declare const __ONE_MEMORY_VERSION__: string;
declare const __ONE_MEMORY_BUILD_NUMBER__: string;
declare const __ONE_MEMORY_COMMIT__: string;

export interface RuntimeCliConfig {
  dataDir: string;
  runtimeEntry: string;
  coreCommand: string;
  coreArgs: string[];
  webDirectory: string;
  mcpEntryPath: string;
  recallCliPath: string;
  syncHookAssetsDirectory: string;
  nodeCommand: string;
  hostHome?: string;
  brokerPort?: number;
  /** Package/build identity when the runtime is running from a built package.
   *  Source checkouts leave it undefined rather than inventing an id. */
  buildIdentity?: {
    version?: string;
    buildId?: string;
    packageId?: string;
    round?: string;
    buildNumber?: string;
    commit?: string;
  };
  hostRuntime?: HostRuntimeAttestation;
  hostRuntimeError?: HostRuntimeErrorCode;
}

type HostRuntimeErrorCode =
  | "host_runtime_current_unavailable"
  | "host_runtime_app_location_unsafe"
  | "host_runtime_current_unsafe"
  | "host_runtime_current_changed"
  | "host_runtime_identity_mismatch"
  | "host_runtime_component_mismatch";
interface HostRuntimeAttestation {
  schemaVersion: 1;
  managedInstallRoot: string;
  selectedVersionRoot: string;
  selectedRelativeTarget: string;
  appMarker: string;
  manifestPackageId: string;
  buildNumber: number;
  platform: RuntimePlatform;
  arch: string;
  manifestSha256: string;
  componentDigests: Record<string, string>;
  runtimeCommand: string;
  mcpEntryPath: string;
  recallCliPath: string;
  captureCommand: string;
  cliEntryPath: string;
  webDirectory: string;
  syncHookAssetsDirectory: string;
}

/** Default Core port. Unchanged for the everyday App path. */
export const DEFAULT_CORE_PORT = 8765;

/** Same shape the Desktop entry uses to opt into an isolated hour. */
const HOURLY_INSTANCE_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/** True only for a genuine isolated-hour package launch.
 *
 * Mirrors `apps/desktop/src/isolated-runtime.ts::isIsolatedHourlyDesktop`: a
 * v4 UUID instance id AND a package id shaped `One-Memory-H*-c7-isolated`. A
 * package that merely looks similar (no marker, or a wrong suffix) is NOT
 * isolated, so it cannot use the port override.
 */
export function isIsolatedHourlyRuntime(
  environment: NodeJS.ProcessEnv,
): boolean {
  const instanceId = environment.ONE_MEMORY_HOURLY_INSTANCE_ID;
  const packageId = environment.ONE_MEMORY_HOURLY_PACKAGE_ID;
  return (
    typeof instanceId === "string" &&
    HOURLY_INSTANCE_RE.test(instanceId) &&
    typeof packageId === "string" &&
    packageId.startsWith("One-Memory-H") &&
    packageId.endsWith("-c7-isolated")
  );
}

/** Decide the Core port for a launch.
 *
 * The everyday App path stays pinned at `DEFAULT_CORE_PORT`. A non-default
 * (custom or isolated) install root may override it with
 * `ONE_MEMORY_CORE_PORT`, but only when the value is a usable port; anything
 * else falls back to the default and reports why.
 *
 * The gate is "this launch is not the everyday install", not "this launch is an
 * isolated hour": the failure this pins is two ordinary custom install roots
 * sharing 8765, and those carry no hourly markers. The daily install never
 * reaches this function with an override set, because `runtimeEnvironment()`
 * does not put `ONE_MEMORY_CORE_PORT` in the daily plist at all.
 */
export function resolveCorePort(environment: NodeJS.ProcessEnv): {
  port: number;
  overrideApplied: boolean;
  ignoredReason: string | null;
} {
  // `ONE_MEMORY_RUNTIME_PORT` is the standalone probe/spawn contract: a CLI
  // harness that starts the Runtime itself must be able to name a non-canonical
  // port without depending on the LaunchAgent's `ONE_MEMORY_CORE_PORT`. The
  // plist name wins when both are set, because it is what the installed
  // service actually launches with.
  const raw =
    environment.ONE_MEMORY_CORE_PORT ?? environment.ONE_MEMORY_RUNTIME_PORT;
  if (raw === undefined || raw === "") {
    return {
      port: DEFAULT_CORE_PORT,
      overrideApplied: false,
      ignoredReason: null,
    };
  }
  // The override is only ever written into a non-default install's plist, so a
  // marker is not required here. Still refuse to honor it when the launch
  // explicitly says it is the daily root, so a stray variable cannot move the
  // everyday port.
  if (isDailyRuntimeRoot(environment)) {
    return {
      port: DEFAULT_CORE_PORT,
      overrideApplied: false,
      ignoredReason: "core_port_override_ignored_daily_root",
    };
  }
  const requested = Number(raw);
  if (!Number.isInteger(requested) || requested < 1024 || requested > 65535) {
    return {
      port: DEFAULT_CORE_PORT,
      overrideApplied: false,
      ignoredReason: "core_port_override_invalid",
    };
  }
  return { port: requested, overrideApplied: true, ignoredReason: null };
}

/** True when the launch names the everyday install root as its own root.
 *
 * The Runtime knows which payload it is running from (`ONE_MEMORY_INSTALL_ROOT`,
 * set by the launcher for a built package). When that equals the platform's
 * default managed root, this is the daily install and must keep 8765. Absent
 * that variable we cannot prove "daily", and the override is still only present
 * because a non-default plist put it there, so the launch is allowed through.
 */
function isDailyRuntimeRoot(environment: NodeJS.ProcessEnv): boolean {
  const declared = environment.ONE_MEMORY_INSTALL_ROOT;
  if (typeof declared !== "string" || declared === "") return false;
  const home = environment.ONE_MEMORY_HOST_HOME ?? environment.HOME;
  if (typeof home !== "string" || home === "") return false;
  const platform: RuntimePlatform =
    process.platform === "darwin"
      ? "darwin"
      : process.platform === "win32"
        ? "win32"
        : "linux";
  const paths = platform === "win32" ? path.win32 : path.posix;
  const daily = paths.resolve(
    defaultManagedInstallRoot(platform, home, environment.LOCALAPPDATA),
  );
  const declaredRoot = paths.resolve(declared);
  // Compare with and without a trailing `current`, since callers name either.
  const normalized =
    paths.basename(declaredRoot) === "current"
      ? paths.dirname(declaredRoot)
      : declaredRoot;
  return normalized === daily;
}

const HOST_RUNTIME_KEYS = [
  "schemaVersion",
  "managedInstallRoot",
  "selectedVersionRoot",
  "selectedRelativeTarget",
  "appMarker",
  "manifestPackageId",
  "buildNumber",
  "platform",
  "arch",
  "manifestSha256",
  "componentDigests",
  "runtimeCommand",
  "mcpEntryPath",
  "recallCliPath",
  "captureCommand",
  "cliEntryPath",
  "webDirectory",
  "syncHookAssetsDirectory",
] as const;
const HOST_RUNTIME_ERRORS = new Set<HostRuntimeErrorCode>([
  "host_runtime_current_unavailable",
  "host_runtime_app_location_unsafe",
  "host_runtime_current_unsafe",
  "host_runtime_current_changed",
  "host_runtime_identity_mismatch",
  "host_runtime_component_mismatch",
]);

function parseHostRuntime(environment: NodeJS.ProcessEnv): {
  hostRuntime?: HostRuntimeAttestation;
  hostRuntimeError?: HostRuntimeErrorCode;
} {
  const blocked = environment.ONE_MEMORY_HOST_RUNTIME_ERROR;
  const raw = environment.ONE_MEMORY_HOST_RUNTIME_ATTESTATION;
  if (blocked !== undefined) {
    if (
      raw !== undefined ||
      !HOST_RUNTIME_ERRORS.has(blocked as HostRuntimeErrorCode)
    )
      throw new Error("host_runtime_attestation_invalid");
    return { hostRuntimeError: blocked as HostRuntimeErrorCode };
  }
  if (raw === undefined) return {};
  if (Buffer.byteLength(raw) > 32768)
    throw new Error("host_runtime_attestation_invalid");
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new Error("host_runtime_attestation_invalid");
  }
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("host_runtime_attestation_invalid");
  const record = value as Record<string, unknown>;
  if (
    JSON.stringify(Object.keys(record)) !== JSON.stringify(HOST_RUNTIME_KEYS) ||
    JSON.stringify(value) !== raw
  )
    throw new Error("host_runtime_attestation_invalid");
  const strings = HOST_RUNTIME_KEYS.filter(
    (key) =>
      !["schemaVersion", "buildNumber", "componentDigests"].includes(key),
  );
  if (
    record.schemaVersion !== 1 ||
    !Number.isSafeInteger(record.buildNumber) ||
    strings.some(
      (key) =>
        typeof record[key] !== "string" ||
        (record[key] as string).length > 4096,
    )
  )
    throw new Error("host_runtime_attestation_invalid");
  if (
    !/^versions\/[0-9A-Za-z][0-9A-Za-z._-]{0,127}$/.test(
      record.selectedRelativeTarget as string,
    ) ||
    !["darwin", "linux"].includes(record.platform as string) ||
    !/^[a-f0-9]{64}$/.test(record.manifestSha256 as string) ||
    !/^One-Memory-HAPP1R[0-9]+(?:\.[0-9]+)*-[a-f0-9]{8}-c7-isolated$/.test(
      record.appMarker as string,
    ) ||
    !/^One-Memory-APP1R[0-9]+(?:\.[0-9]+)*-[a-f0-9]{8}-candidate$/.test(
      record.manifestPackageId as string,
    ) ||
    [
      "managedInstallRoot",
      "selectedVersionRoot",
      "runtimeCommand",
      "mcpEntryPath",
      "recallCliPath",
      "captureCommand",
      "cliEntryPath",
      "webDirectory",
      "syncHookAssetsDirectory",
    ].some((key) => !path.isAbsolute(record[key] as string))
  )
    throw new Error("host_runtime_attestation_invalid");
  if (
    path.normalize(record.managedInstallRoot as string) !==
      record.managedInstallRoot ||
    /[\0\r\n]/.test(record.managedInstallRoot as string)
  )
    throw new Error("host_runtime_attestation_invalid");
  if (
    !record.componentDigests ||
    typeof record.componentDigests !== "object" ||
    Array.isArray(record.componentDigests) ||
    Object.keys(record.componentDigests as object).length > 256 ||
    Object.entries(record.componentDigests as Record<string, unknown>).some(
      ([key, digest]) =>
        !/^[A-Za-z0-9_./-]{1,512}$/.test(key) ||
        typeof digest !== "string" ||
        !/^[a-f0-9]{64}$/.test(digest),
    )
  )
    throw new Error("host_runtime_attestation_invalid");
  const digests = record.componentDigests as Record<string, string>;
  for (const required of [
    "core/one-memory",
    "runtime/runtime.cjs",
    "host-bridge/mcp.cjs",
    "host-bridge/recall-cli.cjs",
    "bin/one-memory.cjs",
    "web/index.html",
    "host-bridge/sync-hook/runtime.mjs",
  ]) {
    if (!(required in digests))
      throw new Error("host_runtime_attestation_invalid");
  }
  if (
    Object.keys(digests).some(
      (key) =>
        ![
          "core/one-memory",
          "runtime/runtime.cjs",
          "host-bridge/mcp.cjs",
          "host-bridge/recall-cli.cjs",
          "bin/one-memory.cjs",
        ].includes(key) &&
        !key.startsWith("web/") &&
        !key.startsWith("host-bridge/sync-hook/"),
    )
  )
    throw new Error("host_runtime_attestation_invalid");
  return { hostRuntime: value as HostRuntimeAttestation };
}

export function resolveRuntimeCliConfig(
  environment: NodeJS.ProcessEnv = process.env,
  executable = process.argv[1] ??
    path.join(process.cwd(), "runtime", "runtime.cjs"),
): RuntimeCliConfig {
  const runtimeRoot = path.resolve(path.dirname(executable), "..");
  const coreExecutable =
    process.platform === "win32" ? "one-memory.exe" : "one-memory";
  // An hourly-package process carries its markers, not the plist's
  // ONE_MEMORY_DATA_DIR; without this it would silently manage the daily root.
  const dataDir =
    hourlyManagedDataDirFromEnvironment(environment, os.homedir()) ??
    path.join(os.homedir(), ".one-memory");
  if (!path.isAbsolute(dataDir) || /[\0\r\n]/.test(dataDir)) {
    throw new Error("runtime_service_data_directory_invalid");
  }
  const hostHome = environment.ONE_MEMORY_HOST_HOME ?? os.homedir();
  if (!path.isAbsolute(hostHome) || /[\0\r\n]/.test(hostHome)) {
    throw new Error("runtime_service_host_home_invalid");
  }
  const hostRuntime = parseHostRuntime(environment);
  const requestedBrokerPort = Number(environment.ONE_MEMORY_BROKER_PORT);
  const brokerPort =
    Number.isSafeInteger(requestedBrokerPort) &&
    requestedBrokerPort >= 1024 &&
    requestedBrokerPort <= 65535 &&
    requestedBrokerPort !== 8765 &&
    requestedBrokerPort !== 18765
      ? requestedBrokerPort
      : undefined;
  return {
    dataDir: path.normalize(dataDir),
    runtimeEntry: path.resolve(
      environment.ONE_MEMORY_RUNTIME_ENTRY?.trim() ||
        path.join(runtimeRoot, "runtime", "runtime.cjs"),
    ),
    coreCommand: path.resolve(
      environment.ONE_MEMORY_CORE_COMMAND?.trim() ||
        path.join(runtimeRoot, "core", coreExecutable),
    ),
    coreArgs: ["serve"],
    webDirectory: path.resolve(
      environment.ONE_MEMORY_WEB_DIR?.trim() || path.join(runtimeRoot, "web"),
    ),
    mcpEntryPath: path.resolve(
      environment.ONE_MEMORY_MCP_ENTRY?.trim() ||
        path.join(runtimeRoot, "host-bridge", "mcp.cjs"),
    ),
    recallCliPath: path.resolve(
      environment.ONE_MEMORY_RECALL_CLI?.trim() ||
        path.join(runtimeRoot, "host-bridge", "recall-cli.cjs"),
    ),
    syncHookAssetsDirectory: path.resolve(
      environment.ONE_MEMORY_SYNC_HOOK_ASSETS?.trim() ||
        path.join(runtimeRoot, "host-bridge", "sync-hook"),
    ),
    nodeCommand: path.resolve(
      environment.ONE_MEMORY_NODE_COMMAND?.trim() || process.execPath,
    ),
    hostHome: path.normalize(hostHome),
    ...(brokerPort === undefined ? {} : { brokerPort }),
    buildIdentity: resolveBuildIdentity(environment),
    ...hostRuntime,
  };
}

/** Package identity for the diagnostic chain. The launcher sets these for a
 *  built package; a source checkout leaves them unset, and we do not invent an
 *  id, so the log never claims an identity the run does not have. */
function resolveBuildIdentity(
  environment: NodeJS.ProcessEnv,
): RuntimeCliConfig["buildIdentity"] {
  const embeddedVersion =
    typeof __ONE_MEMORY_VERSION__ === "string"
      ? __ONE_MEMORY_VERSION__.trim()
      : "";
  const embeddedBuildNumber =
    typeof __ONE_MEMORY_BUILD_NUMBER__ === "string"
      ? __ONE_MEMORY_BUILD_NUMBER__.trim()
      : "";
  const embeddedCommit =
    typeof __ONE_MEMORY_COMMIT__ === "string"
      ? __ONE_MEMORY_COMMIT__.trim()
      : "";
  const version =
    environment.ONE_MEMORY_VERSION?.trim() ||
    (/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/.test(
      embeddedVersion,
    )
      ? embeddedVersion
      : "");
  const buildId = environment.ONE_MEMORY_BUILD_ID?.trim();
  const packageId = environment.ONE_MEMORY_PACKAGE_ID?.trim();
  const round = environment.ONE_MEMORY_ROUND?.trim();
  const buildNumber =
    environment.ONE_MEMORY_BUILD_NUMBER?.trim() ||
    (/^\d+$/.test(embeddedBuildNumber) ? embeddedBuildNumber : "");
  const commit =
    environment.ONE_MEMORY_COMMIT?.trim() ||
    (embeddedCommit && embeddedCommit !== "unknown" ? embeddedCommit : "");
  if (!version && !buildId && !packageId && !round && !buildNumber && !commit)
    return undefined;
  return {
    ...(version ? { version } : {}),
    ...(buildId ? { buildId } : {}),
    ...(packageId ? { packageId } : {}),
    ...(round ? { round } : {}),
    ...(buildNumber ? { buildNumber } : {}),
    ...(commit ? { commit } : {}),
  };
}

export async function revalidateHostRuntime(
  config: RuntimeCliConfig,
): Promise<void> {
  if (config.hostRuntimeError) throw new Error(config.hostRuntimeError);
  const a = config.hostRuntime;
  if (!a) return;
  const root = a.managedInstallRoot;
  const versions = path.join(root, "versions");
  const current = path.join(root, "current");
  for (const directory of [root, versions, a.selectedVersionRoot]) {
    const info = await lstat(directory).catch(() => null);
    if (
      !info?.isDirectory() ||
      info.isSymbolicLink() ||
      (info.mode & 0o022) !== 0 ||
      (process.getuid && info.uid !== process.getuid())
    )
      throw new Error("host_runtime_current_unsafe");
  }
  if (
    (await realpath(root).catch(() => null)) !== root ||
    (await realpath(versions).catch(() => null)) !== versions
  )
    throw new Error("host_runtime_current_unsafe");
  if (path.dirname(a.selectedVersionRoot) !== (await realpath(versions)))
    throw new Error("host_runtime_current_unsafe");
  const link = await readlink(current).catch(() => null);
  if (
    link !== a.selectedRelativeTarget ||
    path.isAbsolute(a.selectedRelativeTarget)
  )
    throw new Error("host_runtime_current_changed");
  if ((await realpath(current).catch(() => null)) !== a.selectedVersionRoot)
    throw new Error("host_runtime_current_changed");
  const manifest = (await verifyInstalledDistribution(
    a.selectedVersionRoot,
    a.platform,
    a.arch,
  )) as {
    build_number?: number;
    package_id?: unknown;
    files: Record<string, string>;
  };
  if (
    manifest.build_number !== a.buildNumber ||
    manifest.package_id !== a.manifestPackageId
  )
    throw new Error("host_runtime_identity_mismatch");
  const family = a.appMarker.match(
    /^One-Memory-H(APP1R[0-9]+(?:\.[0-9]+)*-[0-9a-f]{8})-c7-isolated$/,
  )?.[1];
  if (!family || a.manifestPackageId !== `One-Memory-${family}-candidate`)
    throw new Error("host_runtime_identity_mismatch");
  const expectedPaths = {
    runtimeCommand: path.join(a.selectedVersionRoot, "bin", "node"),
    mcpEntryPath: path.join(a.selectedVersionRoot, "host-bridge", "mcp.cjs"),
    recallCliPath: path.join(
      a.selectedVersionRoot,
      "host-bridge",
      "recall-cli.cjs",
    ),
    captureCommand: path.join(a.selectedVersionRoot, "core", "one-memory"),
    cliEntryPath: path.join(a.selectedVersionRoot, "bin", "one-memory.cjs"),
    webDirectory: path.join(a.selectedVersionRoot, "web"),
    syncHookAssetsDirectory: path.join(
      a.selectedVersionRoot,
      "host-bridge",
      "sync-hook",
    ),
  };
  if (
    Object.entries(expectedPaths).some(
      ([key, expected]) => a[key as keyof typeof expectedPaths] !== expected,
    )
  )
    throw new Error("host_runtime_attestation_invalid");
  if (
    (await fileHash(path.join(a.selectedVersionRoot, "manifest.json"))) !==
    a.manifestSha256
  )
    throw new Error("host_runtime_current_changed");
  for (const [relative, digest] of Object.entries(a.componentDigests)) {
    if (
      manifest.files[relative] !== digest ||
      (await fileHash(path.join(a.selectedVersionRoot, relative))) !== digest
    )
      throw new Error("host_runtime_component_mismatch");
  }
  const node = path.join(a.selectedVersionRoot, "bin", "node");
  const nodeInfo = await lstat(node).catch(() => null);
  if (
    !nodeInfo?.isFile() ||
    nodeInfo.isSymbolicLink() ||
    (nodeInfo.mode & 0o111) === 0
  )
    throw new Error("host_runtime_component_mismatch");
  await access(node, 1);
}

async function fileHash(file: string): Promise<string> {
  const digest = createHash("sha256");
  for await (const chunk of createReadStream(file)) digest.update(chunk);
  return digest.digest("hex");
}

export function guardedPermissions(
  owner: HostPermissions,
  verify: () => Promise<void>,
  attestationDigest: string,
): HostPermissions {
  const guarded = new Set([
    "repairInstalledCommand",
    "repairInstalledCommands",
    "previewInstall",
    "install",
    "setPermission",
    "revokePermission",
    "previewSkillSync",
    "applySkillSync",
    "previewSyncHook",
    "applySyncHook",
    "syncStaleInstalls",
    "refreshInstalledHosts",
  ]);
  return new Proxy(owner, {
    get(target, property, receiver) {
      const value = Reflect.get(target, property, receiver);
      if (
        typeof property !== "string" ||
        !guarded.has(property) ||
        typeof value !== "function"
      )
        return typeof value === "function" ? value.bind(target) : value;
      return async (...args: unknown[]) => {
        await verify();
        if (property === "previewInstall") {
          const preview = await value.apply(target, args);
          return {
            ...preview,
            fingerprint: boundFingerprint(
              preview.fingerprint,
              attestationDigest,
            ),
          };
        }
        if (property === "install") {
          const [layout, approved] = args;
          const preview = await target.previewInstall(layout as never);
          if (
            typeof approved !== "string" ||
            boundFingerprint(preview.fingerprint, attestationDigest) !==
              approved
          )
            throw new Error("host_install_preview_stale");
          await verify();
          return target.install(layout as never, preview.fingerprint);
        }
        return value.apply(target, args);
      };
    },
  });
}

function boundFingerprint(
  fingerprint: string,
  attestationDigest: string,
): string {
  return createHash("sha256")
    .update("host-runtime-v1\0")
    .update(attestationDigest)
    .update("\0")
    .update(fingerprint)
    .digest("hex");
}

export async function createRuntimeService(
  config: RuntimeCliConfig,
): Promise<RuntimeService> {
  const diagnostics = new DiagnosticLogger({
    dataDir: config.dataDir,
    service: "runtime",
    sessionId: process.env.ONE_MEMORY_RUNTIME_SESSION_ID,
  });
  const hostDiagnostics = new DiagnosticLogger({
    dataDir: config.dataDir,
    service: "host-bridge",
  });
  return new RuntimeService({
    dataDir: config.dataDir,
    diagnostics,
    async createSupervisor(onSidecarFault) {
      const { port, overrideApplied, ignoredReason } = resolveCorePort(
        process.env,
      );
      // Only `mode` and `reason` are in the diagnostic allowlist; the concrete
      // port lives in the Core receipt (`runtime-service.json.core_port`), which
      // is the authority the renderer/matrix read anyway.
      await diagnostics.log({
        level: "info",
        component: "runtime.service",
        event: "core.port_resolved",
        outcome: "started",
        details: {
          mode: overrideApplied ? "isolated_override" : "default",
          reason: ignoredReason,
        },
      });
      const capture = await createServiceLogCapture(config.dataDir);
      const core = new CoreSupervisor({
        launch: { command: config.coreCommand, args: config.coreArgs },
        dataDir: config.dataDir,
        port,
        webDirectory: config.webDirectory,
        diagnostics,
        buildIdentity: config.buildIdentity,
        parentSessionId: diagnostics.sessionId,
        onOutput:
          capture === null
            ? undefined
            : (chunk, stream) => {
                void (stream === "stdout"
                  ? capture.writeStdout(chunk)
                  : capture.writeStderr(chunk));
              },
      });
      const recallRuntime = {
        runtimeCommand: config.nodeCommand,
        mcpEntryPath: config.mcpEntryPath,
        discoveryPath: path.join(
          config.dataDir,
          "integrations",
          "cursor-broker.json",
        ),
        recallCliPath: config.recallCliPath,
        captureCommand:
          config.hostRuntime?.captureCommand ?? config.coreCommand,
        hermesProviderSource: path.join(
          path.dirname(config.mcpEntryPath),
          "hermes-provider",
          "hy_memory",
        ),
        dataDir: config.dataDir,
      };
      const permissionOwner = await HostPermissions.open(
        config.dataDir,
        recallRuntime,
        { syncHookAssetsDirectory: config.syncHookAssetsDirectory },
      );
      const verifyHostRuntime = () => revalidateHostRuntime(config);
      const permissions = guardedPermissions(
        permissionOwner,
        verifyHostRuntime,
        config.hostRuntime?.manifestSha256 ?? "daily",
      );
      const hosts = (
        await recallHosts(config.hostHome ?? os.homedir(), {
          ...process.env,
          ONE_MEMORY_HOST_HOME: config.hostHome,
        })
      ).filter((host) => host.hostId !== "openclaw");
      await verifyHostRuntime().then(
        async () => {
          // Every Runtime start after an upgrade, the App's or `memhub
          // upgrade`'s, comes through here first; once per build after that.
          await permissions.refreshInstalledHosts(
            hosts.map((host) => host.layout),
            {
              build:
                config.buildIdentity?.buildNumber ??
                config.buildIdentity?.version,
              diagnostics: hostDiagnostics,
            },
          );
        },
        () => undefined,
      );
      // `RuntimeService` owns the durable receipt and hands down
      // `onSidecarFault`, so a captured Broker fault is published (as
      // `sidecar: "failed"`) for a separate `status` process to read.
      return new RuntimeSupervisor({
        core,
        diagnostics,
        onSidecarFault: () => onSidecarFault(),
        createSidecar(connection, reportFault) {
          const control = createConnectionControlService({
            dataDir: config.dataDir,
            connection,
            permissions,
            hosts,
            layouts: new Map(
              hosts.flatMap((host) =>
                host.hostId === "claude-code"
                  ? ([
                      [host.hostId, host.layout],
                      ["claude", host.layout],
                    ] as const)
                  : ([[host.hostId, host.layout]] as const),
              ),
            ),
          });
          const broker = new RecallBrokerSupervisor({
            discoveryPath: recallRuntime.discoveryPath,
            instanceId: connection.instanceId,
            diagnostics: hostDiagnostics,
            onFault: (code) => reportFault(code),
            createBroker: () =>
              new RecallBroker({
                coreBaseUrl: connection.coreBaseUrl,
                coreApiToken: connection.coreApiToken,
                discoveryPath: recallRuntime.discoveryPath,
                instanceId: connection.instanceId,
                hostGrantStore: permissions.hostGrantStore,
                ...(config.brokerPort === undefined
                  ? {}
                  : { preferredPort: config.brokerPort }),
                diagnostics: (event) => hostDiagnostics.log(event),
                connectionControl: (request) => control.handle(request),
                // A broker-internal fault must degrade the Runtime, not crash it.
                onFault: (code) => reportFault(code),
                sourceNotificationHandler: createSourceChangeHandler({
                  coreBaseUrl: connection.coreBaseUrl,
                  coreApiToken: connection.coreApiToken,
                  instanceId: connection.instanceId,
                }),
              }),
          });
          return {
            start: () => broker.start(),
            async stop() {
              await control.stop();
              await broker.stop();
            },
          };
        },
      });
    },
  });
}

/**
 * The Runtime service runs the SDK migration itself before it spawns Core.
 *
 * Desktop and CLI install/upgrade migrate before they start the service, but a
 * login start, `memhub start`/`restart` and a launchd restart reach this
 * process directly; without this gate they served a home that never finished
 * its upgrade. The service is the Runtime owner here and Core is not running
 * yet, so the maintenance fence (which stops the Runtime and disables its
 * service) must not be used: it would stop this very process.
 */
export async function gateServeOnSdkMigration(
  config: Pick<RuntimeCliConfig, "dataDir" | "coreCommand" | "buildIdentity">,
  migrate: (
    options: SdkMigrationOptions,
  ) => Promise<SdkMigrationResult> = runSdkMigration,
): Promise<SdkMigrationResult> {
  return migrate({
    dataDir: config.dataDir,
    core: { command: config.coreCommand },
    runtimeBuild:
      config.buildIdentity?.buildNumber ??
      config.buildIdentity?.version ??
      "unknown",
    caller: "runtime-service",
    fence: async (_dataDir, action) => action(),
  });
}

/**
 * Run the migration gate until the home is on SDK authority, or the service is
 * asked to stop. Core is never spawned while this has not returned "serve".
 *
 * A failed migration freezes the home (see `runSdkMigration`): the service
 * then exits instead of idling, and its enable marker is off so launchd and
 * systemd do not start it again to retry on their own. A service started on a
 * frozen home (a Desktop login launch, `memhub install` re-registering it)
 * exits the same way without migrating; the next foreground attempt (Desktop
 * launch, `memhub install`/`upgrade`/`start`/`restart`) retries and lifts the
 * freeze. The refusal goes to the migration log and the Runtime diagnostics,
 * the block note tells `status`/`doctor` and Desktop why.
 *
 * When the freeze could not be recorded, the service falls back to staying
 * alive but idle (no Core) until another migrator clears the block note.
 */
export async function holdUntilSdkMigrated(
  config: Pick<RuntimeCliConfig, "dataDir" | "coreCommand" | "buildIdentity">,
  options: {
    stopped: Promise<unknown>;
    migrate?: (options: SdkMigrationOptions) => Promise<SdkMigrationResult>;
    pollIntervalMs?: number;
  },
): Promise<"serve" | "stop"> {
  let stop = false;
  void options.stopped.then(() => {
    stop = true;
  });
  const runtimeBuild =
    config.buildIdentity?.buildNumber ?? config.buildIdentity?.version;
  const refuse = async (code: string, frozen: boolean) => {
    await writeSdkMigrationLog(config.dataDir, {
      writer: "runtime-service",
      event: frozen ? "service.frozen_exit" : "service.core_refused",
      outcome: "refused",
      code,
      runtimeBuild,
    });
    await new DiagnosticLogger({
      dataDir: config.dataDir,
      service: "runtime",
      sessionId: process.env.ONE_MEMORY_RUNTIME_SESSION_ID,
    })
      .log({
        level: "error",
        component: "runtime.service",
        event: "service.start_failed",
        outcome: "blocked",
        error_code: code,
      })
      .catch(() => undefined);
    process.stderr.write(`sdk_migration_required: ${code}\n`);
  };
  const frozenHome = async (): Promise<string | null> => {
    const block = await readSdkMigrationBlock(config.dataDir);
    if (block?.frozen === undefined) return null;
    // Whoever started this service turned the marker on; a frozen home keeps
    // it off so the exit below is not followed by a relaunch.
    await disableRuntimeService(config.dataDir).catch(() => undefined);
    return block.code;
  };
  const frozenAtStart = await frozenHome();
  if (frozenAtStart !== null) {
    await refuse(frozenAtStart, true);
    return "stop";
  }
  for (;;) {
    if (stop) return "stop";
    const migration = await gateServeOnSdkMigration(config, options.migrate);
    if (migration.state !== "deferred") return "serve";
    const code = migration.code ?? "sdk_migration_incomplete";
    const frozen = (await frozenHome()) !== null;
    await refuse(code, frozen);
    if (frozen) return "stop";
    // Wait for a stop, or for another migrator to clear the block note.
    for (;;) {
      const outcome = await Promise.race([
        options.stopped.then(() => "stop" as const),
        delay(options.pollIntervalMs ?? 2_000).then(() => "poll" as const),
      ]);
      if (outcome === "stop") return "stop";
      if ((await readSdkMigrationBlock(config.dataDir)) === null) break;
    }
  }
}

export async function runRuntimeCli(
  arguments_: readonly string[],
  environment: NodeJS.ProcessEnv = process.env,
): Promise<number> {
  if (arguments_[0] === "service-plan") {
    return printServicePlan(arguments_.slice(1), environment);
  }
  if (arguments_[0] === "status") {
    return printStatus(arguments_.slice(1), environment);
  }
  if (
    arguments_.length > 1 ||
    (arguments_[0] !== undefined && arguments_[0] !== "serve")
  ) {
    process.stderr.write(
      "Usage: one-memory-runtime [serve|status [--data-dir <absolute-path>]|service-plan --platform <darwin|linux|win32> --home <path> --install-root <path> [--data-dir <absolute-path>] [--uid <number>] [--xdg-config-home <path>] [--port <1024..65535>]]\n",
    );
    return 2;
  }
  const config = resolveRuntimeCliConfig(environment);
  if (
    await cleanupRemovedAppOwnership({
      dataDir: config.dataDir,
      markerPath: environment.ONE_MEMORY_SERVICE_ENABLE_MARKER,
    })
  ) {
    return 0;
  }
  // Mirror our own stdout/stderr too: with the plist redirect gone, anything the
  // Runtime itself prints would otherwise be lost. Best effort -- a logging
  // problem must never stop the Runtime.
  const ownLogs = await createServiceLogCapture(config.dataDir);
  if (ownLogs !== null) {
    const mirror = (stream: "stdout" | "stderr") => (chunk: Buffer) => {
      void (stream === "stdout"
        ? ownLogs.writeStdout(chunk.toString())
        : ownLogs.writeStderr(chunk.toString()));
    };
    process.stdout.on("data", mirror("stdout"));
    process.stderr.on("data", mirror("stderr"));
  }
  // One listener for the whole life of the process: a stop while waiting on
  // an unfinished migration and a stop while serving are the same request.
  const stopRequested = waitForStopSignal();
  const idleRemoval = new AbortController();
  const held = await holdUntilSdkMigrated(config, {
    stopped: Promise.race([
      stopRequested,
      waitForAppRemoval(
        {
          dataDir: config.dataDir,
          markerPath: environment.ONE_MEMORY_SERVICE_ENABLE_MARKER,
        },
        idleRemoval.signal,
      ),
    ]),
  }).finally(() => idleRemoval.abort());
  if (held === "stop") {
    await ownLogs?.flush();
    return 0;
  }
  const service = await createRuntimeService(config);
  await service.start();
  const appRemoval = new AbortController();
  try {
    await Promise.race([
      stopRequested,
      waitForAppRemoval(
        {
          dataDir: config.dataDir,
          markerPath: environment.ONE_MEMORY_SERVICE_ENABLE_MARKER,
        },
        appRemoval.signal,
      ),
    ]);
  } finally {
    appRemoval.abort();
    await service.stop();
    await ownLogs?.flush();
  }
  return 0;
}

export async function waitForAppRemoval(
  options: {
    dataDir: string;
    markerPath?: string;
    shouldStop?: typeof appOwnedRuntimeShouldStop;
    pollIntervalMs?: number;
  },
  signal: AbortSignal,
): Promise<void> {
  while (!signal.aborted) {
    const continued = await abortableDelay(
      options.pollIntervalMs ?? 2_000,
      signal,
    );
    if (!continued) return;
    const owner = await readRuntimeServiceOwner(options.dataDir).catch(
      () => null,
    );
    if (owner?.owner !== "app") continue;
    if (
      await cleanupRemovedAppOwnership({
        ...options,
        ...(options.shouldStop === undefined
          ? {}
          : { shouldStop: options.shouldStop }),
      })
    ) {
      return;
    }
  }
}

async function abortableDelay(
  milliseconds: number,
  signal: AbortSignal,
): Promise<boolean> {
  if (signal.aborted) return false;
  return new Promise((resolve) => {
    const timer = setTimeout(done, milliseconds);
    const abort = () => done(false);
    function done(continued = true) {
      clearTimeout(timer);
      signal.removeEventListener("abort", abort);
      resolve(continued);
    }
    signal.addEventListener("abort", abort, { once: true });
  });
}

export async function cleanupRemovedAppOwnership(options: {
  dataDir: string;
  markerPath?: string;
  shouldStop?: typeof appOwnedRuntimeShouldStop;
}): Promise<boolean> {
  if (
    !(await (options.shouldStop ?? appOwnedRuntimeShouldStop)({
      dataDir: options.dataDir,
      markerPath: options.markerPath,
    }))
  ) {
    return false;
  }
  const owner = await readRuntimeServiceOwner(options.dataDir).catch(
    () => null,
  );
  if (owner?.owner !== "app") return false;
  await disableRuntimeService(options.dataDir);
  return true;
}

/** Observe the same state the settings page and the tray read.
 *
 * Read-only on purpose: it starts nothing, repairs nothing and prints no
 * token, so it is safe to run while investigating a login-start problem. A
 * non-zero exit distinguishes "not healthy" from "could not tell", because a
 * script that cannot tell must not be read as a pass.
 */
async function printStatus(
  arguments_: readonly string[],
  environment: NodeJS.ProcessEnv,
): Promise<number> {
  const values = optionValues(arguments_);
  const dataDir =
    values?.get("data-dir") ??
    hourlyManagedDataDirFromEnvironment(environment, os.homedir()) ??
    path.join(os.homedir(), ".one-memory");
  const unexpected = [...(values?.keys() ?? [])].some(
    (name) => name !== "data-dir",
  );
  if (!values || unexpected || !path.isAbsolute(dataDir)) {
    process.stderr.write("status requires an absolute data directory\n");
    return 2;
  }
  let health: BackgroundHealth;
  try {
    health = await inspectBackgroundHealth({ dataDir });
  } catch (error) {
    process.stderr.write(
      `${error instanceof Error ? error.message : "background_status_unavailable"}\n`,
    );
    return 2;
  }
  process.stdout.write(
    `${JSON.stringify({ data_dir: dataDir, ...health }, null, 2)}\n`,
  );
  return health.state === "healthy" ? 0 : 4;
}

function printServicePlan(
  arguments_: readonly string[],
  environment: NodeJS.ProcessEnv,
): number {
  const values = optionValues(arguments_);
  if (!values) {
    process.stderr.write("service-plan options are invalid\n");
    return 2;
  }
  const platform = values.get("platform");
  const home = values.get("home") ?? environment.HOME;
  const installRoot = values.get("install-root");
  if (
    (platform !== "darwin" && platform !== "linux" && platform !== "win32") ||
    !home ||
    !installRoot
  ) {
    process.stderr.write(
      "service-plan requires platform, home and install-root\n",
    );
    return 2;
  }
  const uidText = values.get("uid");
  const uid = uidText === undefined ? undefined : Number(uidText);
  if (uidText !== undefined && (!Number.isSafeInteger(uid) || (uid ?? 0) < 1)) {
    process.stderr.write("service-plan uid must be a positive integer\n");
    return 2;
  }
  // An explicit port lets a probe harness compute the exact plan/receipt for a
  // non-canonical port without re-deriving `stableCorePort`. Absent, the layout
  // derives its stable port as before.
  const portText = values.get("port");
  const corePort = portText === undefined ? undefined : Number(portText);
  if (
    portText !== undefined &&
    (!Number.isSafeInteger(corePort) ||
      (corePort ?? 0) < 1024 ||
      (corePort ?? 0) > 65535)
  ) {
    process.stderr.write(
      "service-plan port must be an integer in 1024..65535\n",
    );
    return 2;
  }
  // Keep absence distinct: --home then owns the default data directory.
  const dataDir = values.get("data-dir") ?? environment.ONE_MEMORY_DATA_DIR;
  const paths = platform === "win32" ? path.win32 : path.posix;
  if (
    dataDir !== undefined &&
    (!paths.isAbsolute(dataDir) || /[\0\r\n]/.test(dataDir))
  ) {
    process.stderr.write(
      "service-plan data directory must be an absolute path without control characters\n",
    );
    return 2;
  }
  const plan = planUserService(
    runtimeInstallLayout({
      platform: platform as RuntimePlatform,
      home,
      installRoot,
      dataDir,
      ...(corePort === undefined ? {} : { corePort }),
      ...hourlyIsolationFromEnvironment(environment),
    }),
    { uid, xdgConfigHome: values.get("xdg-config-home") },
  );
  process.stdout.write(`${JSON.stringify(plan, null, 2)}\n`);
  return plan.status === "ready" ? 0 : 4;
}

function optionValues(
  arguments_: readonly string[],
): Map<string, string> | null {
  const result = new Map<string, string>();
  const allowed = new Set([
    "platform",
    "home",
    "install-root",
    "data-dir",
    "uid",
    "xdg-config-home",
    "port",
  ]);
  for (let index = 0; index < arguments_.length; index += 2) {
    const key = arguments_[index];
    const value = arguments_[index + 1];
    const name = key?.startsWith("--") ? key.slice(2) : "";
    if (
      !allowed.has(name) ||
      result.has(name) ||
      !value ||
      value.startsWith("--")
    ) {
      return null;
    }
    result.set(name, value);
  }
  return result;
}

async function waitForStopSignal(): Promise<void> {
  await new Promise<void>((resolve) => {
    const stop = () => {
      process.off("SIGINT", stop);
      process.off("SIGTERM", stop);
      resolve();
    };
    process.once("SIGINT", stop);
    process.once("SIGTERM", stop);
  });
}
