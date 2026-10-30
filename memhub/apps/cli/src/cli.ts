import { spawn } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { createReadStream } from "node:fs";
import { access } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {
  type HealthResponse,
  type ImportJob,
  isHostId,
} from "@one-memory/contracts";
import {
  type AcquiredRuntimeDistribution,
  acquireRuntimeDistribution,
  applyLocalInstall,
  applyLocalUninstall,
  type BackgroundHealth,
  canonicalManagedServiceRoot,
  defaultManagedInstallRoot,
  diagnosticZipFilename,
  discoverRuntimeAttachment,
  ensureRuntimeProcess,
  exportDiagnosticZip,
  hourlyIsolationFromEnvironment,
  inspectBackgroundHealth,
  inspectRuntimeStatus,
  installCliLink,
  planCliLink,
  planLocalInstall,
  planLocalUninstall,
  planUserService,
  processIsAlive,
  type ResolvedRuntimeRelease,
  RuntimeDiscoveryError,
  readInstalledRuntimeSource,
  readSdkMigrationBlock,
  removeRuntimeServiceOwner,
  resolveRuntimeCliConfig,
  resolveRuntimeRelease,
  restartUserService,
  rollbackCliLink,
  rollbackLocalInstall,
  runRuntimeCli,
  runSdkMigration,
  runtimeInstallLayout,
  type SdkMigrationResult,
  type SdkMigrationSource,
  sdkMigrationLogPath,
  stopRuntimeProcess,
} from "@one-memory/runtime-control";
import axios from "axios";

declare const __ONE_MEMORY_BUILD_NUMBER__: string;
declare const __ONE_MEMORY_COMMIT__: string;
declare const __MEMHUB_DEFAULT_RELEASE_MANIFEST_URL_TEMPLATE__: string;

import {
  callRestore,
  type RestoreResult,
  verifyMaintenanceCoreIdentity,
} from "./backup-restore.js";
import {
  type BatchScanDocument,
  batchScanExitCode,
  detectedSourceIds,
  executeBatchImport,
  planBatchImport,
  renderBatchScanText,
} from "./batch-import-flow.js";
import { readBoundedFile, readBoundedJson } from "./bounded-file.js";
import {
  type CommandDescriptor,
  findCommand,
  findCommandOrAlias,
} from "./command-catalog.js";
import {
  CONTEXT_QUERY_MAX_CHARACTERS,
  type ContextSearchDocument,
  contextExitCode,
  parseContextText,
  readBoundedStdin,
  renderContextText,
  searchContext,
} from "./context-search.js";
import {
  addScanRoot,
  type CoreContextQuery,
  type CoreReader,
  createCoreContextQuery,
  createCoreReader,
  createUiSessionRequest,
  deleteModelProfile,
  type MemoryWriteReceipt,
  parseOpenAIMessages,
  probeAdapter,
  readAdapters,
  readMemoryWrite,
  readScanRoots,
  removeScanRoot,
  resetOnboarding,
  submitBackup,
  submitMemoryWrite,
  submitScan,
} from "./core-client.js";
import {
  type CoreDeleteOperations,
  type CoreOperations,
  type CorePatchOperations,
  type CorePutOperations,
  createCoreOperations,
} from "./core-operations.js";
import {
  MEMORY_STATUSES,
  type SourceConnectionCandidate,
  validateImportJob,
  validateSourceConnectionCandidates,
} from "./core-responses.js";
import { applyDataDeletion, planDataDeletion } from "./data-deletion.js";
import {
  composeDoctorReport,
  type DoctorDocument,
  doctorExitCode,
  renderDoctorText,
} from "./doctor-report.js";
import {
  EXIT_FAILED,
  EXIT_OK,
  EXIT_PLAN_BLOCKED,
  EXIT_RUNTIME_STOPPED,
  EXIT_USAGE,
} from "./exit-codes.js";
import {
  decideGraphWidth,
  GRAPH_WIDTH_DECISIONS,
  type GraphWidthDecision,
  graphWidthDecisionIsDestructive,
  graphWidthExitCode,
  renderGraphWidthText,
} from "./graph-width-flow.js";
import {
  connectSourcesWithImport,
  createModelProfile,
  type FlowDocument,
  flowExitCode,
  parseApiKey,
  readModelReceipt,
  readSourceReceipt,
  renderFlowText,
} from "./headless-flows.js";
import {
  type HostFlowDocument,
  type HostOperation,
  hostExitCode,
  listHosts,
  readHost,
  renderHostText,
} from "./host-flow.js";
import {
  createTerminalPrompter,
  runInitWizard,
  type WizardPrompter,
} from "./init-wizard.js";
import {
  defaultInstallReceiptPath,
  removeInstallReceipt,
  resolveInstalledDataDir,
  writeInstallReceipt,
} from "./install-receipt.js";
import { runInteractiveModelSetup } from "./interactive-model-setup.js";
import {
  executeJobAction,
  executeJobStart,
  type JobFlowDocument,
  jobExitCode,
  planJobAction,
  planJobStart,
} from "./job-flow.js";
import {
  followLogs,
  parseLogQuery,
  readLogReport,
  redactDiagnosticEvent,
  renderLogText,
} from "./log-report.js";
import {
  installManagedCliLink,
  planManagedCliLink,
  removeManagedCliLink,
  rollbackManagedCliLink,
} from "./managed-cli-link.js";
import {
  addMemory,
  isMemoryId,
  isRequestId,
  MEMORY_KINDS,
  MEMORY_SCENES,
  MEMORY_VARIANTS,
  type MemoryKind,
  type MemoryNamespace,
  type MemoryQuery,
  type MemoryScene,
  type MemoryVariant,
  memoryAddExitCode,
  namespaceParameters,
  parseMemoryContent,
  readMemoryAddStatus,
  readMemoryDetail,
  readMemoryPage,
  renderMemoryAddText,
  renderMemoryDetailText,
  renderMemoryPageText,
  validMemoryText,
} from "./memory-report.js";
import {
  type ModelProbeFlowDocument,
  modelProbeExitCode,
  readModelProbeReceipt,
  renderModelProbeText,
  startModelProbe,
} from "./model-probe-flow.js";
import {
  type ModelReportDocument,
  modelReportExitCode,
  readModelProfile,
  readModelProfiles,
  renderModelReportText,
} from "./model-report.js";
import {
  activateModelProfile,
  editModelProfile,
  type ModelWriteDocument,
  modelWriteExitCode,
  renderModelWriteText,
} from "./model-write-flows.js";
import {
  parseBackupCommand,
  parseScanCommand,
  parseScanRootsCommand,
  parseWriteCommand,
  restoreConfirmationMatches,
} from "./product-flows.js";
import { QUERY_MAX_CHARACTERS, readQueryFile } from "./query-file.js";
import { inspectRuntimeServiceLock } from "./runtime-lock-guard.js";
import {
  restoreManagedServiceEnabled,
  setManagedServiceEnabled,
} from "./service-enable-state.js";
import {
  applyPreparedServiceInstall,
  prepareServiceInstall,
  rollbackPreparedServiceInstall,
  uninstallServicePreservingData,
} from "./service-lifecycle.js";
import {
  claimCliServiceOwnership,
  restoreServiceOwnership,
} from "./service-owner-state.js";
import {
  createTolerantLaunchctlRunner,
  inspectLaunchdService,
  restartManagedService,
  runLaunchctlCaptured,
  startManagedService,
  stopManagedService,
} from "./service-session.js";
import {
  applyShellPath,
  planShellPath,
  removeShellPath,
} from "./shell-path.js";
import {
  type ProgressDocument,
  readProgress,
  readSources,
  readSync,
  renderProgressText,
  renderSourcesText,
  renderSyncText,
  type SourcesDocument,
  type SyncDocument,
  sourceProgressExitCode,
  unreadProgress,
  unreadSources,
  unreadSync,
} from "./source-progress-report.js";
import {
  renderSourceSyncText,
  type SourceSyncFlowDocument,
  setSourceSync,
  sourceSyncExitCode,
} from "./source-sync-flow.js";
import {
  composeStatusReport,
  renderStatusText,
  type StatusDocument,
  statusExitCode,
} from "./status-report.js";
import {
  openBrowser,
  parseUiSessionExchange,
  type UiResult,
  uiExitCode,
  uiSessionExchangePath,
} from "./ui-session.js";
import {
  renderCatalogDocument,
  renderCommandHelp,
  renderGeneralHelp,
  renderUsageError,
  renderUsageErrorDocument,
  renderVersion,
  renderVersionDocument,
} from "./usage.js";
import { CLI_VERSION, PRODUCT, RUNTIME_RELEASE_VERSION } from "./version.js";
import { runWatchLoop, type WatchEvent, type WatchRead } from "./watch-loop.js";

export interface RunControls {
  /** Platform used by read-only platform probes. Production uses
   * `process.platform`; tests inject it instead of mutating the global. */
  platform?: NodeJS.Platform;
  watchMaxEvents?: number;
  watchSignal?: AbortSignal;
  watchWait?: (milliseconds: number, signal: AbortSignal) => Promise<void>;
  watchNow?: () => string;
  readStdin?: () => Promise<Buffer | null>;
  stdinIsTTY?: boolean;
  /** Injectable onboarding prompter so the wizard can be driven in tests
   * without a real terminal. Production uses `createTerminalPrompter`. */
  wizardPrompter?: WizardPrompter;
  /** Wait for a restarted Runtime to become healthy after `upgrade`.
   * Injectable so the upgrade tests do not spend real seconds polling. */
  waitForRuntimeReady?: (dataDir: string) => Promise<boolean>;
  /** Run the SDK data migration before install/upgrade starts the new
   * Runtime. Injectable so the tests do not spawn a real Core. */
  migrateSdkData?: typeof migrateSdkDataBeforeStart;
  restartManagedRuntime?: typeof restartManagedService;
  stopRuntime?: typeof stopRuntimeProcess;
  ensureRuntime?: typeof ensureRuntimeProcess;
  /** The launchd probe `status` uses to catch a service-managed Runtime whose
   * receipts vanished. Defaults to the real `launchctl` inspector; tests that
   * inject a stopped runtime replace it so they never read the host's global
   * LaunchAgent label. */
  inspectLaunchd?: (dataDir: string) => Promise<{
    state: "running" | "loaded" | "foreign" | "absent" | "unknown";
    pid?: number;
  }>;
  /** Inject the browser opener so `ui` can be tested without launching one. */
  openBrowser?: (url: string) => Promise<boolean>;
  resolveRuntimeRelease?: typeof resolveRuntimeRelease;
  acquireRuntimeDistribution?: typeof acquireRuntimeDistribution;
  /** Drive `backup restore`: start a non-owning maintenance Core, call the
   * restore endpoint, stop it. Injectable so tests never spawn a process. */
  runMaintenanceRestore?: (options: {
    coreCommand: string;
    dataDir: string;
    source: string;
  }) => Promise<
    | { state: "restored"; result: RestoreResult }
    | { state: "rejected"; code: string }
  >;
}

type InstallSourceSelection =
  | { kind: "local"; source: string }
  | {
      kind: "remote";
      manifestUrl: string;
      channel: "stable" | "prerelease";
      allowedHosts?: readonly string[];
    };

const embeddedReleaseManifestTemplate =
  typeof __MEMHUB_DEFAULT_RELEASE_MANIFEST_URL_TEMPLATE__ === "string"
    ? __MEMHUB_DEFAULT_RELEASE_MANIFEST_URL_TEMPLATE__.trim()
    : "";

async function selectInstallSource(
  parsed: ReturnType<typeof parseInstallOptions> & object,
  environment: NodeJS.ProcessEnv,
): Promise<InstallSourceSelection> {
  if (
    parsed.sourceExplicit ||
    environment.MEMHUB_TEST_FORCE_LOCAL_INSTALL_SOURCE === "1"
  ) {
    return { kind: "local", source: parsed.source };
  }
  if (await regularFile(path.join(parsed.source, "manifest.json"))) {
    return { kind: "local", source: parsed.source };
  }
  if (CLI_VERSION === "development") {
    throw new Error("release_version_unavailable");
  }
  const explicitManifestUrl =
    parsed.releaseManifestUrl ??
    environment.MEMHUB_RELEASE_MANIFEST_URL?.trim();
  const template =
    environment.MEMHUB_RELEASE_MANIFEST_URL_TEMPLATE?.trim() ||
    embeddedReleaseManifestTemplate;
  if (!explicitManifestUrl && !template) {
    throw new Error("release_manifest_url_unavailable");
  }
  const manifestUrl =
    explicitManifestUrl ??
    template.replaceAll("{version}", RUNTIME_RELEASE_VERSION);
  const allowedHosts = parseAllowedReleaseHosts(
    environment.MEMHUB_RELEASE_ALLOWED_HOSTS,
  );
  return {
    kind: "remote",
    manifestUrl,
    channel: parsed.releaseChannel,
    ...(allowedHosts ? { allowedHosts } : {}),
  };
}

async function regularFile(file: string): Promise<boolean> {
  try {
    await access(file);
    return true;
  } catch {
    return false;
  }
}

function parseAllowedReleaseHosts(
  value: string | undefined,
): string[] | undefined {
  if (value === undefined || value.trim() === "") return undefined;
  const hosts = value
    .split(",")
    .map((host) => host.trim().toLowerCase())
    .filter(Boolean);
  if (
    hosts.length === 0 ||
    hosts.length > 16 ||
    hosts.some((host) => !/^[a-z0-9.-]+$/.test(host))
  ) {
    throw new Error("release_allowed_hosts_invalid");
  }
  return [...new Set(hosts)];
}

async function resolveRemoteInstallPreview(
  selection: Extract<InstallSourceSelection, { kind: "remote" }>,
  controls: RunControls,
): Promise<ResolvedRuntimeRelease> {
  return (controls.resolveRuntimeRelease ?? resolveRuntimeRelease)({
    manifestUrl: selection.manifestUrl,
    expectedVersion: RUNTIME_RELEASE_VERSION,
    channel: selection.channel,
    platform: process.platform,
    arch: process.arch,
    allowedHosts: selection.allowedHosts,
  });
}

async function acquireRemoteInstallSource(
  selection: Extract<InstallSourceSelection, { kind: "remote" }>,
  controls: RunControls,
): Promise<AcquiredRuntimeDistribution> {
  return (controls.acquireRuntimeDistribution ?? acquireRuntimeDistribution)({
    manifestUrl: selection.manifestUrl,
    expectedVersion: RUNTIME_RELEASE_VERSION,
    channel: selection.channel,
    platform: process.platform,
    arch: process.arch,
    allowedHosts: selection.allowedHosts,
  });
}

function remoteReleasePlan(resolved: ResolvedRuntimeRelease) {
  return {
    schema_version: 1,
    source: "release",
    manifest_url: resolved.manifest_url,
    channel: resolved.release.channel,
    version: resolved.release.version,
    build_number: resolved.release.build_number,
    git_commit: resolved.release.git_commit,
    cli_protocol: resolved.release.cli_protocol,
    runtime_protocol: resolved.release.runtime_protocol,
    platform: resolved.artifact.platform,
    arch: resolved.artifact.arch,
    artifact: resolved.artifact.filename,
    download_bytes: resolved.artifact.bytes,
    install_bytes: resolved.artifact.unpacked_bytes,
    sha256: resolved.artifact.sha256,
  } as const;
}

export async function run(
  arguments_: readonly string[],
  environment: NodeJS.ProcessEnv = process.env,
  controls: RunControls = {},
): Promise<number> {
  return runCommand(arguments_, environment, controls, false);
}

/** @internal Retains legacy install paths for focused safety tests only. */
export async function runWithManagedInstallForTest(
  arguments_: readonly string[],
  environment: NodeJS.ProcessEnv = process.env,
  controls: RunControls = {},
): Promise<number> {
  return runCommand(
    arguments_,
    { ...environment, MEMHUB_TEST_FORCE_LOCAL_INSTALL_SOURCE: "1" },
    controls,
    false,
  );
}

async function runCommand(
  arguments_: readonly string[],
  environment: NodeJS.ProcessEnv,
  controls: RunControls,
  localExperienceMode: boolean,
): Promise<number> {
  // `--json` is documented as a global option, so a caller may put it before
  // the command (`memhub --json status`). Normalise it to the end so every
  // existing `arguments_.slice(1)` parser keeps seeing the command first.
  if (arguments_[0] === "--json") {
    arguments_ = [...arguments_.slice(1), "--json"];
  }
  arguments_ = rewriteLegacyCommand(arguments_);
  const asJson = arguments_.includes("--json");
  // `--data-dir <path>` is a global option: every Runtime-attached command
  // reads its data directory from ONE_MEMORY_DATA_DIR, so the flag is lifted
  // into the environment before dispatch. The install family, service-* and
  // delete-data parse it themselves (it has command-specific meaning there).
  if (!OWN_DATA_DIR_COMMANDS.has(arguments_[0] ?? "")) {
    const lifted = liftDataDirOption(arguments_);
    if (lifted === null) {
      return reject(findCommand(arguments_[0] ?? ""), asJson, "参数无效。");
    }
    arguments_ = lifted.arguments_;
    if (lifted.dataDir !== undefined) {
      environment = { ...environment, ONE_MEMORY_DATA_DIR: lifted.dataDir };
    }
  }
  environment = await environmentWithManagedDataDir(environment);
  const command = arguments_[0];
  if (command === undefined) return reject(undefined, asJson, "缺少命令。");
  if (command === "--help" || command === "-h" || command === "help") {
    return explain(arguments_.slice(1), asJson);
  }
  const named =
    command === "--version" || command === "-V" ? "version" : command;
  const descriptor = findCommand(named);
  if (!descriptor) return reject(undefined, asJson, "未知命令。");
  const usage = () => reject(descriptor, asJson, "参数无效。");
  const help = helpRequest(arguments_.slice(1));
  if (help === "invalid") return reject(descriptor, asJson, "参数无效。");
  if (help === "help") {
    return emit(
      asJson
        ? renderCatalogDocument(descriptor)
        : renderCommandHelp(descriptor),
    );
  }
  if (named === "version") {
    return arguments_.length === (asJson ? 2 : 1)
      ? emit(asJson ? renderVersionDocument() : renderVersion())
      : reject(descriptor, asJson, "参数无效。");
  }
  if (named === "models" && arguments_[1] === "setup") {
    if (
      arguments_.length !== 2 ||
      asJson ||
      !(controls.stdinIsTTY ?? process.stdin.isTTY)
    ) {
      return usage();
    }
    const config = resolveRuntimeCliConfig(environment, process.argv[1]);
    const runtime = await inspectRuntimeStatus({
      dataDir: config.dataDir,
      probeIdentity,
      requireServiceReceipt: true,
    });
    if (runtime.state !== "running") {
      emit(
        `无法配置模型：${runtime.code}\n下一步：先运行 ${PRODUCT} start 启动 Runtime。\n`,
      );
      return runtime.state === "stopped" ? EXIT_RUNTIME_STOPPED : EXIT_FAILED;
    }
    const clients = await attachClients(config.dataDir);
    if (!clients.reader || !clients.operations) return EXIT_FAILED;
    const prompter =
      controls.wizardPrompter ?? (await createTerminalPrompter());
    try {
      const result = await runInteractiveModelSetup(
        prompter,
        { reader: clients.reader, operations: clients.operations },
        {
          dataDir: config.dataDir,
          hostHome: config.hostHome ?? os.homedir(),
          installHosts: false,
        },
      );
      return result.state === "failed" ? EXIT_FAILED : EXIT_OK;
    } finally {
      (prompter as { close?: () => void }).close?.();
    }
  }
  // Commands that attach to the Runtime directly. They answer a stopped
  // Runtime with exit 3, a Core refusal with exit 1 and a JSON document on
  // stdout (never a usage error: the arguments were fine).
  if (named === "adapter") {
    const tokens = arguments_.slice(1).filter((item) => item !== "--json");
    if (arguments_.filter((item) => item === "--json").length > 1) {
      return usage();
    }
    const probe =
      tokens.length === 3 &&
      tokens[0] === "probe" &&
      tokens[1] === "--source" &&
      CONTEXT_SOURCE_ID.test(tokens[2]);
    if (!probe && !(tokens.length === 1 && tokens[0] === "list")) {
      return usage();
    }
    const attachment = await attachToRuntimeOrNull(environment);
    if (attachment === null) return runtimeUnavailable(descriptor, asJson);
    if (probe) {
      const result = await probeAdapter(attachment, tokens[2]);
      if (!result.ok) {
        return emitFailure(asJson, "adapter probe", result.code);
      }
      emit(
        asJson
          ? `${JSON.stringify({ schema_version: 1, product: PRODUCT, command: "adapter probe", state: "ok", probe: result.data })}\n`
          : renderAdapterProbeText(tokens[2], result.data),
      );
      return EXIT_OK;
    }
    const document = await readAdapters(attachment);
    if (document === null) {
      return emitFailure(asJson, "adapter list", "adapters_unavailable");
    }
    if (asJson) {
      process.stdout.write(`${JSON.stringify(document)}\n`);
      return EXIT_OK;
    }
    const adapters = document.adapters as Array<{
      source_id: string;
      available: boolean;
      authorized: boolean;
      capabilities: Record<string, string>;
    }>;
    const lines = adapters.map(
      (adapter) =>
        `  ${adapter.source_id}  available=${adapter.available} authorized=${adapter.authorized} ` +
        `live_sync=${adapter.capabilities.live_sync ?? "?"} recall=${adapter.capabilities.recall ?? "?"}`,
    );
    process.stdout.write(`Harness 适配器：\n${lines.join("\n")}\n`);
    return EXIT_OK;
  }
  // Back to the cold-start screens on purpose. Deleting the app cannot do this
  // (the completion flag lives with the memories, by design), and deleting the
  // data to get there would cost the user everything they have stored.
  if (named === "onboarding") {
    if (
      arguments_.filter((item) => item === "--json").length > 1 ||
      arguments_.filter((item) => item !== "--json").join(" ") !==
        "onboarding reset"
    ) {
      return usage();
    }
    const attachment = await attachToRuntimeOrNull(environment);
    if (attachment === null) return runtimeUnavailable(descriptor, asJson);
    const state = await resetOnboarding(attachment);
    if (state === null) {
      return emitFailure(asJson, "onboarding reset", "onboarding_unavailable");
    }
    if (asJson) {
      process.stdout.write(`${JSON.stringify(state)}\n`);
      return EXIT_OK;
    }
    process.stdout.write(
      `引导流程已回到首次运行（status=${state.status}），记忆数据未改动。\n`,
    );
    return EXIT_OK;
  }
  if (named === "scan-roots") {
    const isList =
      arguments_.filter((item) => item !== "--json").join(" ") ===
        "scan-roots list" &&
      arguments_.filter((item) => item === "--json").length <= 1;
    const parsed = isList ? null : parseScanRootsCommand(arguments_.slice(1));
    if (!isList && parsed === null) return usage();
    const attachment = await attachToRuntimeOrNull(environment);
    if (attachment === null) return runtimeUnavailable(descriptor, asJson);
    if (parsed === null) {
      const document = await readScanRoots(attachment);
      if (document === null) {
        return emitFailure(asJson, "scan-roots list", "scan_roots_unavailable");
      }
      if (asJson) {
        process.stdout.write(`${JSON.stringify(document)}\n`);
        return EXIT_OK;
      }
      const roots = document.roots as Array<{
        path: string;
        source_id: string | null;
        recursive: boolean;
        enabled: boolean;
      }>;
      if (roots.length === 0) {
        process.stdout.write("尚未登记任何扫描目录。\n");
        return EXIT_OK;
      }
      const lines = roots.map(
        (root) =>
          `  ${root.path}  source=${root.source_id ?? "auto"} recursive=${root.recursive} enabled=${root.enabled}`,
      );
      process.stdout.write(
        `已登记的扫描目录（revision ${document.revision}）：\n${lines.join("\n")}\n`,
      );
      return EXIT_OK;
    }
    const result =
      parsed.kind === "add"
        ? await addScanRoot(attachment, {
            root: parsed.root,
            ...(parsed.source === undefined ? {} : { source: parsed.source }),
            recursive: parsed.recursive,
          })
        : await removeScanRoot(attachment, { root: parsed.root });
    const verb = `scan-roots ${parsed.kind}`;
    if (!result.ok) return emitFailure(asJson, verb, result.code);
    emit(
      asJson
        ? `${JSON.stringify({ schema_version: 1, product: PRODUCT, command: verb, state: "committed", root: parsed.root, result: result.data })}\n`
        : parsed.kind === "add"
          ? `已登记扫描目录：${parsed.root}\n`
          : `已移除扫描目录：${parsed.root}\n`,
    );
    return EXIT_OK;
  }
  if (named === "scan") {
    const parsed = parseScanCommand(arguments_.slice(1));
    if (!parsed) return usage();
    if (parsed.kind === "all") {
      return await connectSources(
        { kind: "all", importHistory: true, confirmOverrides: false },
        asJson,
        environment,
      );
    }
    return await applyScan(asJson, descriptor, environment, parsed.sourceId);
  }
  if (named === "backup") {
    const parsed = parseBackupCommand(arguments_.slice(1));
    if (!parsed) return usage();
    if (parsed.verb === "create") {
      return await applyBackupCreate(asJson, descriptor, environment, parsed);
    }
    return await applyBackupRestore(asJson, environment, controls, parsed);
  }
  if (named === "write") {
    const parsed = parseWriteCommand(arguments_.slice(1));
    if (!parsed) return usage();
    if (parsed.kind === "submit" && parsed.request_id !== undefined) {
      if (!isWriteRequestId(parsed.request_id)) return usage();
    }
    const messages =
      parsed.kind === "submit"
        ? parseOpenAIMessages(await readBoundedJson(parsed.messages_file))
        : null;
    if (parsed.kind === "submit" && messages === null) {
      return reject(
        descriptor,
        asJson,
        "messages 文件必须是含 user/assistant/tool 文本消息的 OpenAI messages 数组。",
      );
    }
    const attachment = await attachToRuntimeOrNull(environment);
    if (attachment === null) return runtimeUnavailable(descriptor, asJson);
    if (parsed.kind === "status") {
      const receipt = await readMemoryWrite(attachment, parsed.id).catch(
        () => undefined,
      );
      if (receipt === undefined) {
        return emitFailure(asJson, "write status", "memory_write_read_failed");
      }
      if (receipt === null) {
        // Unknown id: "never accepted", not "accepted and failed". It is still
        // not an answer the caller asked for, so it exits 1 with a state.
        emit(
          asJson
            ? `${JSON.stringify({ schema_version: 1, product: PRODUCT, command: "write status", state: "not_found", request_id: parsed.id, write: null })}\n`
            : `未找到该 request id：${parsed.id}\n`,
        );
        return EXIT_FAILED;
      }
      emit(
        asJson
          ? `${JSON.stringify({ schema_version: 1, product: PRODUCT, command: "write status", state: "ok", write: receipt })}\n`
          : renderWriteReceiptText(receipt),
      );
      return EXIT_OK;
    }
    // The request id is Core's idempotency key. Generate one when the caller
    // gave none, and always print it, so a retry can reuse it instead of
    // writing the same conversation twice.
    const requestId = parsed.request_id ?? randomUUID();
    const outcome = await submitMemoryWrite(attachment, {
      session: parsed.session,
      source: parsed.source,
      request_id: requestId,
      messages: messages ?? [],
      ...(parsed.memory_at === undefined
        ? {}
        : { memory_at: parsed.memory_at }),
      ...(parsed.priority === undefined ? {} : { priority: parsed.priority }),
    });
    if (outcome.state !== "accepted") {
      emit(
        asJson
          ? `${JSON.stringify({ schema_version: 1, product: PRODUCT, command: "write submit", state: outcome.state, request_id: requestId, code: outcome.code })}\n`
          : `写入未完成：${outcome.state}（${outcome.code}）\n${
              outcome.state === "unknown"
                ? `结果未知；用同一个 --request-id ${requestId} 重试不会重复写入。\n`
                : ""
            }`,
      );
      return EXIT_FAILED;
    }
    emit(
      asJson
        ? `${JSON.stringify({ schema_version: 1, product: PRODUCT, command: "write submit", state: "accepted", write: outcome.receipt })}\n`
        : `${renderWriteReceiptText(outcome.receipt)}重试请带上同一个 --request-id ${outcome.receipt.request_id}，不会重复写入。\n`,
    );
    return EXIT_OK;
  }
  if (named === "jobs") {
    const parsed = parseJobsCommand(arguments_.slice(1));
    if (!parsed) return usage();
    const connected = await connectRuntime(environment);
    if (!connected.ok) {
      emitJobUnavailable(connected.code, asJson);
      return connected.exitCode;
    }
    const { reader, operations } = connected;
    if (parsed.kind === "list") {
      const document = await readJobs(reader);
      emitJobDocument(document, asJson);
      return jobExitCode(document);
    }
    if (parsed.kind === "show") {
      const document = await readJob(reader, parsed.jobId);
      emitJobDocument(document, asJson);
      return jobExitCode(document);
    }
    const planned =
      parsed.kind === "start"
        ? planJobStart(parsed.sourceId, parsed.operation)
        : planJobAction(parsed.jobId, parsed.action);
    if (planned.state !== "preview") {
      emitJobDocument(planned, asJson);
      return jobExitCode(planned);
    }
    const document =
      planned.plan.kind === "import_job_start"
        ? await executeJobStart(planned.plan, operations)
        : await executeJobAction(planned.plan, operations);
    emitJobDocument(document, asJson);
    return jobExitCode(document);
  }
  if (named === "logs") {
    if (arguments_[1] === "export") {
      if (arguments_.slice(2).some((value) => value !== "--json"))
        return usage();
      const config = resolveRuntimeCliConfig(environment, process.argv[1]);
      const downloads = path.resolve(
        environment.ONE_MEMORY_EXPORT_DIR ??
          path.join(os.homedir(), "Downloads"),
      );
      const build =
        environment.ONE_MEMORY_BUILD_NUMBER?.trim() ||
        (typeof __ONE_MEMORY_BUILD_NUMBER__ === "string"
          ? __ONE_MEMORY_BUILD_NUMBER__
          : "unknown");
      const summary = await diagnosticSummary(config).catch(() => ({
        entry: "cli",
      }));
      const target = path.join(downloads, diagnosticZipFilename(build));
      const environmentCommit = environment.ONE_MEMORY_COMMIT?.trim();
      const embeddedCommit =
        typeof __ONE_MEMORY_COMMIT__ === "string"
          ? __ONE_MEMORY_COMMIT__.trim()
          : "";
      const commit =
        environmentCommit ||
        (embeddedCommit && embeddedCommit !== "unknown"
          ? embeddedCommit
          : undefined);
      const receipt = await exportDiagnosticZip({
        dataDir: config.dataDir,
        target,
        productVersion: build,
        ...(commit === undefined ? {} : { commit }),
        coreSha256: await sha256File(config.coreCommand).catch(() => undefined),
        home: os.homedir(),
        crashReportsDir: path.join(
          os.homedir(),
          "Library",
          "Logs",
          "DiagnosticReports",
        ),
        summary,
      });
      process.stdout.write(
        asJson
          ? `${JSON.stringify({ schema_version: 1, command: "logs export", state: "exported", path: target, file_count: receipt.file_count })}\n`
          : `日志已导出：${target}\n`,
      );
      return EXIT_OK;
    }
    const parsed = parseLogQuery(arguments_.slice(1));
    if (!parsed) return usage();
    const config = resolveRuntimeCliConfig(environment, process.argv[1]);
    if (!parsed.follow) {
      const report = await readLogReport(config.dataDir, parsed);
      if (report.failure !== undefined) {
        // A `logs` path that is not a usable directory is a real fault, not an
        // empty result: report it instead of emitting zero bytes at exit 0.
        if (asJson) {
          process.stderr.write(
            `${JSON.stringify({
              schema_version: 1,
              product: PRODUCT,
              command: "logs",
              status: "error",
              code: report.failure,
              exit_code: EXIT_FAILED,
              next_step:
                "检查数据目录下的 logs 是否为可读目录；必要时运行 memhub doctor。",
            })}\n`,
          );
        } else {
          process.stderr.write(
            `${PRODUCT}: 日志目录不可读（${report.failure}）。\n`,
          );
        }
        return EXIT_FAILED;
      }
      emit(
        asJson
          ? report.events.length
            ? `${report.events
                .map((event) => JSON.stringify(redactDiagnosticEvent(event)))
                .join("\n")}\n`
            : `${JSON.stringify({
                schema_version: 1,
                product: PRODUCT,
                command: "logs",
                state: "empty",
                events: 0,
                invalid_lines: report.invalid_lines,
              })}\n`
          : renderLogText(report.events, report.invalid_lines),
      );
      if (report.invalid_lines) {
        process.stderr.write(
          `${PRODUCT}: 跳过 ${report.invalid_lines} 行损坏的诊断记录。\n`,
        );
      }
      return EXIT_OK;
    }
    const owned = controls.watchSignal ? null : new AbortController();
    const signal = controls.watchSignal ?? owned?.signal;
    if (!signal) return EXIT_FAILED;
    const cancel = () => owned?.abort();
    if (owned) {
      process.once("SIGINT", cancel);
      process.once("SIGTERM", cancel);
    }
    try {
      await followLogs({
        dataDir: config.dataDir,
        query: parsed,
        signal,
        maxEvents: controls.watchMaxEvents,
        wait: controls.watchWait,
        emit: (event) =>
          emit(
            asJson
              ? `${JSON.stringify(redactDiagnosticEvent(event))}\n`
              : renderLogText([event], 0),
          ),
      });
      return EXIT_OK;
    } finally {
      if (owned) {
        process.off("SIGINT", cancel);
        process.off("SIGTERM", cancel);
      }
    }
  }
  if (named === "serve") {
    if (arguments_.length !== 1) return usage();
    return runRuntimeCli(["serve"], environment);
  }
  if (command === "start" || command === "restart") {
    // Accept the global `--json`; the objective requires stable JSON on every
    // declared command, and lifecycle is exactly where scripts need it.
    if (arguments_.length !== (asJson ? 2 : 1)) return usage();
    const config = resolveRuntimeCliConfig(environment, process.argv[1]);
    // A failed upgrade migration froze this home: its service exits without
    // migrating. `start`/`restart` is an explicit retry, run here in the
    // foreground; success lifts the freeze and turns the service back on.
    const frozen = await readSdkMigrationBlock(config.dataDir).catch(
      () => null,
    );
    if (frozen?.frozen !== undefined) {
      const migration = await (
        controls.migrateSdkData ?? migrateSdkDataBeforeStart
      )({
        coreCommand: config.coreCommand,
        dataDir: config.dataDir,
        runtimeBuild:
          config.buildIdentity?.buildNumber ??
          config.buildIdentity?.version ??
          "unknown",
        asJson,
        caller: command === "restart" ? "cli-restart" : "cli-start",
      });
      if (migration.state === "deferred") {
        const failure = sdkMigrationFailure(
          command,
          migration.code ?? "sdk_migration_incomplete",
          config.dataDir,
          "blocked",
          migration.dataRestored === true,
        );
        process.stdout.write(
          asJson
            ? `${JSON.stringify({ ...failure, state: "blocked" })}\n`
            : `${failure.message}\n`,
        );
        return EXIT_FAILED;
      }
    }
    const enableSnapshot = await setManagedServiceEnabled(config.dataDir, true);
    const restoreEnableState = () =>
      restoreManagedServiceEnabled(config.dataDir, enableSnapshot);
    const lifecycle = (
      state: "started" | "restarted" | "already_running" | "not-ready",
      code: string,
    ) =>
      asJson
        ? `${JSON.stringify({
            schema_version: 1,
            product: PRODUCT,
            command,
            state,
            code,
          })}\n`
        : state === "started"
          ? command === "restart"
            ? "MemHub Runtime 已重启（用户服务）。\n"
            : "MemHub Runtime 已启动（用户服务）。\n"
          : state === "already_running"
            ? "MemHub Runtime 已在运行。\n"
            : "Runtime 服务已注册但未就绪；请运行 memhub logs 查看原因。\n";
    // A Runtime that did not come up because its service refuses to serve an
    // unmigrated home reports that cause, the migration log and where to send
    // it, instead of a generic budget timeout.
    const migrationBlocked = async (): Promise<string | null> => {
      const blocked = await runtimeMigrationBlock(config.dataDir, command);
      if (blocked === null) return null;
      return asJson
        ? `${JSON.stringify({
            schema_version: 1,
            product: PRODUCT,
            command,
            state: "blocked",
            code: blocked.code,
            message: blocked.message,
            log_file: blocked.log_file,
            issues_url: blocked.issues_url,
          })}\n`
        : `${blocked.message}\n`;
    };
    const notReady = async () =>
      (await migrationBlocked()) ??
      lifecycle("not-ready", "runtime_not_ready_within_budget");
    // A launchd-managed Runtime must be driven through launchctl. Spawning a
    // second process while the service holds the data directory is exactly what
    // produced "Runtime exited before becoming ready".
    const uid = process.getuid?.();
    const serviceId =
      process.platform === "darwin" && uid !== undefined
        ? managedRuntimeServiceId(config, environment, uid)
        : null;
    const service =
      serviceId !== null && uid !== undefined
        ? {
            serviceId,
            uid,
            home: config.hostHome ?? os.homedir(),
            runCaptured: runLaunchctlCaptured,
            // Only a label whose plist binds this data dir is ours.
            dataDir: config.dataDir,
          }
        : null;
    let stoppedForRestart = false;
    if (command === "restart") {
      if (service) {
        // Restart keeps the launchd registration: kickstart -k, not bootout.
        let restarted: Awaited<ReturnType<typeof restartManagedService>>;
        try {
          restarted = await (
            controls.restartManagedRuntime ?? restartManagedService
          )(service);
        } catch (error) {
          await restoreEnableState();
          throw error;
        }
        if (restarted === "restarted") {
          // launchd `state = running` only means a process was spawned, not
          // that Core came up: the Runtime supervisor still has to publish its
          // receipts and pass the identity probe. Reporting success here would
          // reproduce the "started but nothing answers" split.
          const ready = await (
            controls.waitForRuntimeReady ?? waitForRuntimeReady
          )(config.dataDir);
          if (ready) {
            process.stdout.write(lifecycle("restarted", "runtime_verified"));
            return EXIT_OK;
          }
          process.stdout.write(await notReady());
          await restoreEnableState();
          return EXIT_FAILED;
        }
        if (restarted === "not-ready") {
          process.stdout.write(await notReady());
          await restoreEnableState();
          return EXIT_FAILED;
        }
        if (enableSnapshot.managed) {
          await restoreEnableState();
          process.stdout.write(
            lifecycle("not-ready", "runtime_service_declaration_missing"),
          );
          return EXIT_FAILED;
        }
        // "absent": no registered service; fall through to the receipt path.
      }
      let stopped: Awaited<ReturnType<typeof stopRuntimeProcess>>;
      try {
        stopped = await (controls.stopRuntime ?? stopRuntimeProcess)({
          dataDir: config.dataDir,
          probeIdentity,
        });
      } catch (error) {
        await restoreEnableState();
        throw error;
      }
      stoppedForRestart = stopped === "stopped";
      if (stopped === "not-running") {
        // A note on stderr in JSON mode, so stdout stays a single document.
        (asJson ? process.stderr : process.stdout).write(
          asJson
            ? `${JSON.stringify({
                schema_version: 1,
                product: PRODUCT,
                command: "restart",
                state: "not-running",
                code: "runtime_not_running",
              })}\n`
            : "MemHub Runtime 原先未运行，将直接启动。\n",
        );
      }
    }
    if (service) {
      let startedManaged: Awaited<ReturnType<typeof startManagedService>>;
      try {
        startedManaged = await startManagedService(service);
      } catch (error) {
        await restoreEnableState();
        throw error;
      }
      if (startedManaged === "started") {
        // Same as restart: a launchd pid is not proof Core is ready.
        const ready = await (
          controls.waitForRuntimeReady ?? waitForRuntimeReady
        )(config.dataDir);
        if (ready) {
          process.stdout.write(lifecycle("started", "runtime_verified"));
          return EXIT_OK;
        }
        process.stdout.write(await notReady());
        await restoreEnableState();
        return EXIT_FAILED;
      }
      if (startedManaged === "not-ready") {
        process.stdout.write(await notReady());
        await restoreEnableState();
        return EXIT_FAILED;
      }
      if (enableSnapshot.managed) {
        await restoreEnableState();
        process.stdout.write(
          lifecycle("not-ready", "runtime_service_declaration_missing"),
        );
        return EXIT_FAILED;
      }
      // "absent": no service registered -> fall through to the receipt path.
    }
    let result: "attached" | "started";
    try {
      result = await (controls.ensureRuntime ?? ensureRuntimeProcess)({
        dataDir: config.dataDir,
        launch: {
          command: config.nodeCommand,
          args: [config.runtimeEntry, "serve"],
          env: runtimeEnvironment(config),
        },
        probeIdentity,
      });
    } catch (error) {
      await restoreEnableState();
      // A spawned Runtime that exits before ready is a failure of this command,
      // not a crash: report it as a structured document (JSON) or a plain
      // message, with a non-zero exit, instead of letting it escape.
      const blocked = await migrationBlocked();
      if (blocked !== null) {
        process.stdout.write(blocked);
        return EXIT_FAILED;
      }
      const message = error instanceof Error ? error.message : String(error);
      process.stdout.write(
        asJson
          ? `${JSON.stringify({
              schema_version: 1,
              product: PRODUCT,
              command,
              state: "not-ready",
              code: "runtime_exited_before_ready",
              message,
            })}\n`
          : `${message}\n`,
      );
      return EXIT_FAILED;
    }
    process.stdout.write(
      lifecycle(
        result === "attached"
          ? "already_running"
          : stoppedForRestart
            ? "restarted"
            : "started",
        result === "attached"
          ? "runtime_attached"
          : stoppedForRestart
            ? "runtime_restarted"
            : "runtime_started",
      ),
    );
    return 0;
  }
  if (command === "stop") {
    if (arguments_.length !== (asJson ? 2 : 1)) return usage();
    const config = resolveRuntimeCliConfig(environment, process.argv[1]);
    const enableSnapshot = await setManagedServiceEnabled(
      config.dataDir,
      false,
    );
    const restoreEnableState = () =>
      restoreManagedServiceEnabled(config.dataDir, enableSnapshot);
    // A managed service is kept alive by an enable marker; stopping it must
    // clear that marker or launchd/systemd would restart it at once. The
    // marker is also what starts it at login, so say plainly that login
    // auto-start is now off and that `start` turns it back on.
    const autoStartNote = enableSnapshot.managed
      ? "登录时自动启动已关闭；运行 memhub start 会重新启动并恢复自动启动。\n"
      : "";
    const stoppedDoc = (
      state: "stopped" | "not-running" | "still-running",
      code: string,
    ) =>
      asJson
        ? `${JSON.stringify({
            schema_version: 1,
            product: PRODUCT,
            command: "stop",
            state,
            code,
            ...(enableSnapshot.managed && state !== "still-running"
              ? { login_auto_start: false }
              : {}),
          })}\n`
        : state === "stopped"
          ? `MemHub Runtime 已停止。\n${autoStartNote}`
          : state === "still-running"
            ? "Runtime 由用户服务托管，停用被拒绝或被立即拉起；请运行 memhub service-uninstall。\n"
            : `MemHub Runtime 未运行。\n${autoStartNote}`;
    // When the Runtime is a launchd-managed user service, a receipt-based stop
    // is wrong: SIGTERM to the receipt pid is immediately undone by KeepAlive,
    // and the receipts can be deleted while the daemon lives on. Stop through
    // launchctl instead, and never report success unless the label is gone.
    const uid = process.getuid?.();
    if (process.platform === "darwin" && uid !== undefined) {
      const serviceId = managedRuntimeServiceId(config, environment, uid);
      let managed: Awaited<ReturnType<typeof stopManagedService>>;
      try {
        managed = await stopManagedService({
          serviceId,
          uid,
          runCaptured: runLaunchctlCaptured,
          // Only a label whose plist binds *this* data dir is ours to stop.
          dataDir: config.dataDir,
        });
      } catch (error) {
        await restoreEnableState();
        throw error;
      }
      if (managed === "still-running") {
        await restoreEnableState();
        process.stdout.write(
          stoppedDoc("still-running", "runtime_service_still_running"),
        );
        return EXIT_FAILED;
      }
      if (managed === "stopped") {
        process.stdout.write(stoppedDoc("stopped", "runtime_stopped"));
        return EXIT_OK;
      }
      // "not-running": no managed service; fall through to the receipt path.
    }
    let result: Awaited<ReturnType<typeof stopRuntimeProcess>>;
    try {
      result = await stopRuntimeProcess({
        dataDir: config.dataDir,
        probeIdentity,
      });
    } catch (error) {
      await restoreEnableState();
      throw error;
    }
    process.stdout.write(
      result === "stopped"
        ? stoppedDoc("stopped", "runtime_stopped")
        : stoppedDoc("not-running", "runtime_not_running"),
    );
    return 0;
  }
  if (named === "status" || named === "doctor") {
    const parsed = parseStatusCommand(named, arguments_.slice(1));
    if (!parsed) return usage();
    const config = resolveRuntimeCliConfig(environment, process.argv[1]);
    const serviceId =
      (controls.platform ?? process.platform) === "darwin" &&
      process.getuid?.() !== undefined
        ? managedRuntimeServiceId(
            config,
            environment,
            process.getuid?.() as number,
          )
        : null;
    if (parsed.watch) {
      return runCliWatch({
        kind: "status",
        intervalMs: parsed.intervalMs,
        asJson,
        controls,
        read: (signal) =>
          readStatusSnapshot(
            config.dataDir,
            signal,
            controls.inspectLaunchd,
            controls.platform,
            serviceId ?? undefined,
          ),
        render: (report) => renderStatusText(report, "Runtime 状态"),
      });
    }
    if (named === "doctor") {
      const { report, runtimeStopped } = await readDoctorSnapshot(
        config.dataDir,
      );
      emit(asJson ? `${JSON.stringify(report)}\n` : renderDoctorText(report));
      // Same exit 3 as `status` when the Runtime is simply not running.
      return runtimeStopped ? EXIT_RUNTIME_STOPPED : doctorExitCode(report);
    }
    const snapshot = await readStatusSnapshot(
      config.dataDir,
      undefined,
      controls.inspectLaunchd,
      controls.platform,
      serviceId ?? undefined,
    );
    const report = snapshot.value;
    emit(
      asJson
        ? `${JSON.stringify(report)}\n`
        : renderStatusText(report, "Runtime 状态"),
    );
    return statusExitCode(report);
  }
  if (named === "ui") {
    if (arguments_.length !== (asJson ? 2 : 1)) return usage();
    // Contract: docs/contracts/c0-ui-and-restore-contract.md (C0, H25).
    // Core owns session establishment: it hands back a one-time code URL and
    // converts it to an HttpOnly/SameSite=Strict cookie. The CLI never puts the
    // Core bearer token in a URL — it only forwards the loopback URL Core
    // minted. If Core predates this contract the endpoint 404s and we report
    // the stable, honest "contract unavailable" block.
    const config = resolveRuntimeCliConfig(environment, process.argv[1]);
    const attachment = await attachToRuntime(config.dataDir);
    const emitUi = (result: UiResult, detail?: string) => {
      const document =
        result.state === "opened"
          ? {
              schema_version: 1 as const,
              product: PRODUCT,
              command: "ui",
              state: result.state,
              url: result.url,
              ...(detail === undefined ? {} : { next_step: detail }),
            }
          : {
              schema_version: 1 as const,
              product: PRODUCT,
              command: "ui",
              state: result.state,
              code: result.code,
              ...(detail === undefined ? {} : { next_step: detail }),
            };
      emit(
        asJson
          ? `${JSON.stringify(document)}\n`
          : result.state === "opened"
            ? `已在系统浏览器打开 Web Console：${result.url}\n`
            : `ui 未打开：${result.code}\n${detail === undefined ? "" : `下一步：${detail}\n`}`,
      );
      return uiExitCode(result);
    };
    if (attachment === null) {
      const migration = await runtimeMigrationBlock(config.dataDir, "ui");
      if (migration !== null) {
        emit(
          asJson
            ? `${JSON.stringify({
                schema_version: 1,
                product: PRODUCT,
                command: "ui",
                state: "blocked",
                code: migration.code,
                message: migration.message,
                log_file: migration.log_file,
                issues_url: migration.issues_url,
              })}\n`
            : `ui 未打开：${migration.code}\n${migration.message}\n`,
        );
        return uiExitCode({ state: "blocked", code: migration.code });
      }
      return emitUi(
        { state: "blocked", code: "runtime_not_running" },
        `先运行 ${PRODUCT} start 启动 Runtime。`,
      );
    }
    const exchange = await requestUiSession(attachment).catch(() => null);
    if (exchange === null) {
      return emitUi(
        { state: "blocked", code: "ui_session_transport_failed" },
        "无法连接 Core 的 loopback session 端点；确认 Runtime 可用后重试。",
      );
    }
    if ("code" in exchange) {
      return emitUi({ state: "blocked", code: exchange.code });
    }
    const opened = await (controls.openBrowser ?? openBrowser)(exchange.url);
    return emitUi(
      opened
        ? { state: "opened", url: exchange.url }
        : { state: "failed", code: "ui_browser_open_failed" },
      opened ? undefined : "无法调用系统默认浏览器；请手动打开上面的地址。",
    );
  }
  if (named === "host") {
    const parsed = parseHostCommand(arguments_.slice(1));
    if (!parsed) return usage();
    const config = resolveRuntimeCliConfig(environment, process.argv[1]);
    // `list` names the hosts on this machine, so it needs no Runtime. Every
    // other subcommand goes through it, because that is where the layouts and
    // every write to a host's config live.
    if (parsed.kind === "host-list") {
      const document = await listHosts(config.hostHome ?? os.homedir());
      emitHostDocument(document, asJson);
      return EXIT_OK;
    }
    const runtime = await inspectRuntimeStatus({
      dataDir: config.dataDir,
      probeIdentity,
      requireServiceReceipt: true,
    });
    if (runtime.state !== "running") {
      emitHostDocument(
        {
          schema_version: 1,
          product: PRODUCT,
          command: parsed.kind === "host-apply" ? "apply" : "status",
          host_id: parsed.hostId,
          state: "unavailable",
          code: runtime.code,
        },
        asJson,
      );
      return runtime.state === "stopped" ? 3 : 1;
    }
    let document: HostFlowDocument;
    try {
      document = await readHost(
        config.dataDir,
        parsed.hostId,
        parsed.operation,
        parsed.args,
      );
    } catch (error) {
      const code =
        error instanceof Error ? error.message : "host_operation_failed";
      emitHostDocument(
        {
          schema_version: 1,
          product: PRODUCT,
          command: parsed.kind === "host-apply" ? "apply" : "status",
          host_id: parsed.hostId,
          state: "unavailable",
          code,
        },
        asJson,
      );
      return EXIT_FAILED;
    }
    emitHostDocument(document, asJson);
    return hostExitCode(document);
  }
  if (named === "recall") {
    const parsed = parseContextCommand(arguments_.slice(1));
    if (!parsed) return usage();
    let queryText = parsed.query;
    if (parsed.queryFile) {
      queryText = await readQueryFile(
        parsed.queryFile,
        CONTEXT_QUERY_MAX_CHARACTERS,
      );
    } else if (parsed.stdin) {
      if (controls.stdinIsTTY ?? process.stdin.isTTY === true) return usage();
      const bytes = await (controls.readStdin ?? readBoundedStdin)();
      queryText = bytes ? parseContextText(bytes) : null;
    }
    if (queryText === null) return usage();
    const config = resolveRuntimeCliConfig(environment, process.argv[1]);
    const runtime = await inspectRuntimeStatus({
      dataDir: config.dataDir,
      probeIdentity,
      requireServiceReceipt: true,
    });
    if (runtime.state !== "running") {
      const document: ContextSearchDocument = {
        schema_version: 2,
        product: PRODUCT,
        state: "unavailable",
        code: runtime.code,
        items: [],
      };
      emit(
        asJson ? `${JSON.stringify(document)}\n` : renderContextText(document),
      );
      return runtime.state === "stopped" ? 3 : 1;
    }
    const attached = await attachContextQuery(config.dataDir);
    const document = attached.query
      ? await searchContext(attached.query, {
          query: queryText,
          kinds: parsed.kinds,
          ...(parsed.sources.length > 0
            ? { raw_source_ids: parsed.sources }
            : {}),
          limit: parsed.limit,
          character_budget: parsed.characterBudget,
          timeout_ms: parsed.timeoutMs,
        })
      : ({
          schema_version: 2,
          product: PRODUCT,
          state: "unavailable",
          code: attached.failure ?? "runtime_attach_failed",
          items: [],
        } satisfies ContextSearchDocument);
    emit(
      asJson ? `${JSON.stringify(document)}\n` : renderContextText(document),
    );
    return contextExitCode(document);
  }
  if (
    named === "models" &&
    (arguments_[1] === "list" || arguments_[1] === "show")
  ) {
    const parsed = parseModelReadCommand(arguments_.slice(1));
    if (!parsed) return usage();
    const config = resolveRuntimeCliConfig(environment, process.argv[1]);
    const runtime = await inspectRuntimeStatus({
      dataDir: config.dataDir,
      probeIdentity,
      requireServiceReceipt: true,
    });
    if (runtime.state !== "running") {
      const document: ModelReportDocument = {
        schema_version: 1,
        product: PRODUCT,
        state: "unavailable",
        code: runtime.code,
      };
      emitModelReportDocument(document, asJson);
      return runtime.state === "stopped" ? 3 : 1;
    }
    const attached = await attachReader(config.dataDir);
    const document: ModelReportDocument = attached.reader
      ? parsed.kind === "list"
        ? await readModelProfiles(attached.reader)
        : await readModelProfile(attached.reader, parsed.profileId)
      : {
          schema_version: 1,
          product: PRODUCT,
          state: "unavailable",
          code: attached.failure ?? "runtime_attach_failed",
        };
    emitModelReportDocument(document, asJson);
    return modelReportExitCode(document);
  }
  if (named === "models") {
    const parsed = parseModelsCommand(arguments_.slice(1));
    if (!parsed) return usage();
    // Read every local input before touching the Runtime, so a bad file is a
    // usage error that changed nothing.
    const config =
      parsed.kind === "add" || parsed.kind === "edit"
        ? await readBoundedJson(parsed.configFile)
        : null;
    if ((parsed.kind === "add" || parsed.kind === "edit") && config === null) {
      return usage();
    }
    let apiKey: string | undefined;
    if (
      (parsed.kind === "add" || parsed.kind === "edit") &&
      parsed.apiKeyFile !== undefined
    ) {
      apiKey = parseApiKey(
        await readBoundedFile(parsed.apiKeyFile, 16 * 1024 + 2, {
          ownerOnly: true,
        }),
      );
      if (apiKey === undefined) return usage();
    }
    if (parsed.kind === "remove" && !parsed.confirmed) {
      return emitConfirmPreview(asJson, {
        command: "models remove",
        target: parsed.profileId,
        effect: `删除模型配置 ${parsed.profileId}（正在使用的配置不能删除）`,
        retry: `${PRODUCT} models remove ${parsed.profileId} --yes`,
      });
    }
    const connected = await connectRuntime(environment);
    if (!connected.ok) {
      emitFlowDocument(
        {
          schema_version: 1,
          product: PRODUCT,
          state: "unknown",
          code: connected.code,
        },
        asJson,
      );
      return connected.exitCode;
    }
    const { reader, operations } = connected;
    if (parsed.kind === "add") {
      const created = await createModelProfile(
        config,
        apiKey,
        reader,
        operations,
      );
      if (
        !parsed.activate ||
        (created.state !== "committed" && created.state !== "recovered")
      ) {
        emitFlowDocument(created, asJson);
        return flowExitCode(created);
      }
      const activated = await activateModelProfile(
        String(created.receipt.profile_id),
        operations,
      );
      const document = {
        ...created,
        activation:
          activated.state === "committed"
            ? { state: "committed" }
            : { state: activated.state, code: activated.code },
      };
      emit(
        asJson
          ? `${JSON.stringify(document)}\n`
          : `${renderFlowText(created)}${
              activated.state === "committed"
                ? "已设为当前使用的模型配置。\n"
                : `设为当前使用未完成：${activated.state}（${activated.code}）\n`
            }`,
      );
      return activated.state === "committed" ? EXIT_OK : EXIT_FAILED;
    }
    if (parsed.kind === "edit" || parsed.kind === "activate") {
      const document =
        parsed.kind === "edit"
          ? await editModelProfile(parsed.profileId, config, apiKey, operations)
          : await activateModelProfile(parsed.profileId, operations);
      emitModelWriteDocument(document, asJson);
      return modelWriteExitCode(document);
    }
    if (parsed.kind === "test") {
      const document = await startModelProbe(
        reader,
        operations,
        parsed.profileId,
      );
      emitModelProbeDocument(document, asJson);
      return modelProbeExitCode(document);
    }
    if (parsed.kind === "receipt") {
      return await emitModelReceipt(reader, parsed.operationId, asJson);
    }
    const removed = await removeModelProfile(
      connected.dataDir,
      parsed.profileId,
    );
    if (removed === "not_found" || removed === "active") {
      // Two reasons, two next actions: pick another id, or stop using it
      // first. Naming which one keeps the user from retrying blindly.
      emit(
        asJson
          ? `${JSON.stringify({
              schema_version: 1,
              product: PRODUCT,
              command: "models remove",
              state: "rejected",
              code:
                removed === "active"
                  ? "active_model_profile"
                  : "model_profile_not_found",
            })}\n`
          : removed === "active"
            ? "删除失败：该模型配置正在使用中，请先激活另一个配置后再删除。\n"
            : "删除失败：未找到该模型配置。\n",
      );
      return EXIT_FAILED;
    }
    if (removed === "unavailable") {
      return emitFailure(
        asJson,
        "models remove",
        "model_remove_result_unknown",
      );
    }
    emit(
      asJson
        ? `${JSON.stringify({
            schema_version: 1,
            product: PRODUCT,
            command: "models remove",
            state: "committed",
            profile_id: parsed.profileId,
          })}\n`
        : `已删除模型配置：${parsed.profileId}\n`,
    );
    return EXIT_OK;
  }
  if (named === "sources" && arguments_[1] !== "list") {
    const parsed = parseSourcesCommand(arguments_.slice(1));
    if (!parsed) return usage();
    if (parsed.kind === "receipt") {
      const connected = await connectRuntime(environment);
      if (!connected.ok) {
        emitFlowDocument(
          {
            schema_version: 1,
            product: PRODUCT,
            state: "unknown",
            code: connected.code,
          },
          asJson,
        );
        return connected.exitCode;
      }
      const document = await readSourceReceipt(
        connected.reader,
        parsed.requestId,
      );
      emitFlowDocument(document, asJson);
      return flowExitCode(document);
    }
    return await connectSources(parsed, asJson, environment);
  }
  if (named === "init") {
    // `init` is the friendly first run: on a terminal it walks the wizard,
    // elsewhere it lists what can be connected and the exact next command.
    // Scripted connects go through `sources connect` (the old
    // `init --source/--all-detected` spellings are rewritten to it).
    if (
      arguments_.filter((item) => item === "--json").length > 1 ||
      arguments_.slice(1).some((item) => item !== "--json")
    ) {
      return usage();
    }
    const connected = await connectRuntime(environment);
    if (!connected.ok) {
      emit(
        asJson
          ? `${JSON.stringify({
              schema_version: 1,
              product: PRODUCT,
              command: "init",
              state: "unavailable",
              code: connected.code,
            })}\n`
          : `未接入：${connected.code}\n${
              connected.exitCode === EXIT_RUNTIME_STOPPED
                ? `下一步：先运行 ${PRODUCT} start 启动 Runtime。\n`
                : ""
            }`,
      );
      return connected.exitCode;
    }
    const outcome = await connected.reader<unknown>("/api/v1/sources");
    const candidates =
      outcome.state === "ok"
        ? validateSourceConnectionCandidates(outcome.data)
        : null;
    if (candidates === null) {
      const code =
        outcome.state === "ok" ? "source_response_invalid" : outcome.code;
      emit(
        asJson
          ? `${JSON.stringify({
              schema_version: 1,
              product: PRODUCT,
              command: "init",
              state: "unavailable",
              code,
            })}\n`
          : `未接入：${code}\n`,
      );
      return EXIT_FAILED;
    }
    // Interactive only when a real terminal is attached and JSON was not
    // asked for (a machine-readable run must never wait on a prompt).
    const interactive =
      !asJson && (controls.stdinIsTTY ?? process.stdin.isTTY) === true;
    if (interactive && candidates.length > 0) {
      return await runInteractiveInit(
        candidates,
        { reader: connected.reader, operations: connected.operations },
        asJson,
        emit,
        controls.wizardPrompter,
        connected.dataDir,
        resolveRuntimeCliConfig(environment, process.argv[1]).hostHome ??
          os.homedir(),
      );
    }
    if (asJson) {
      emit(
        `${JSON.stringify({
          schema_version: 1,
          product: PRODUCT,
          command: "init",
          state: "choose",
          candidates: candidates.map((candidate) => ({
            source_id: candidate.source_id,
            display_name: candidate.display_name,
            requires_override_confirmation:
              candidate.requires_override_confirmation,
          })),
          next_step: `${PRODUCT} sources connect <id> [<id> ...] --import [--confirm-overrides]`,
        })}\n`,
      );
      return EXIT_OK;
    }
    if (candidates.length === 0) {
      emit("本机没有检测到可接入的来源。\n");
      return EXIT_OK;
    }
    const lines = candidates.map(
      (candidate) =>
        `  ${candidate.source_id}${
          candidate.requires_override_confirmation
            ? "  （目录来自环境变量覆盖，需加 --confirm-overrides）"
            : ""
        }  ${candidate.display_name}`,
    );
    emit(
      `可接入的来源：\n${lines.join("\n")}\n\n下一步：${PRODUCT} sources connect <id> [<id> ...] --import\n`,
    );
    return EXIT_OK;
  }
  if (named === "sources" || named === "sync" || named === "progress") {
    const parsed = parseReadCommand(named, arguments_.slice(1));
    if (!parsed) return usage();
    if (parsed.kind === "sync-set") {
      const connected = await connectRuntime(environment);
      if (!connected.ok) {
        const document: SourceSyncFlowDocument = {
          schema_version: 1,
          product: PRODUCT,
          state: "unknown",
          code: connected.code,
        };
        emitSourceSyncDocument(document, asJson);
        return connected.exitCode;
      }
      const document = await setSourceSync(
        connected.reader,
        connected.operations,
        {
          sourceId: parsed.sourceId,
          action: parsed.action,
        },
      );
      emitSourceSyncDocument(document, asJson);
      return sourceSyncExitCode(document);
    }
    const config = resolveRuntimeCliConfig(environment, process.argv[1]);
    if (parsed.kind === "progress" && parsed.watch) {
      return runCliWatch({
        kind: "progress",
        intervalMs: parsed.intervalMs,
        asJson,
        controls,
        read: (signal) => readProgressSnapshot(config.dataDir, parsed, signal),
        render: renderProgressText,
      });
    }
    const runtime = await inspectRuntimeStatus({
      dataDir: config.dataDir,
      probeIdentity,
      requireServiceReceipt: true,
    });
    if (runtime.state !== "running") {
      const code = runtime.code;
      const document =
        named === "sources"
          ? unreadSources("not_checked", code)
          : named === "sync"
            ? unreadSync("not_checked", code)
            : unreadProgress("not_checked", code);
      emitReadDocument(named, document, asJson);
      return runtime.state === "stopped" ? 3 : 1;
    }
    const attached = await attachReader(config.dataDir);
    if (!attached.reader) {
      const code = attached.failure ?? "runtime_attach_failed";
      const document =
        named === "sources"
          ? unreadSources("unavailable", code)
          : named === "sync"
            ? unreadSync("unavailable", code)
            : unreadProgress("unavailable", code);
      emitReadDocument(named, document, asJson);
      return EXIT_FAILED;
    }
    const document =
      parsed.kind === "sources"
        ? await readSources(attached.reader)
        : parsed.kind === "sync"
          ? await readSync(attached.reader)
          : await readProgress(attached.reader, parsed);
    emitReadDocument(named, document, asJson);
    return sourceProgressExitCode(document);
  }
  if (named === "profile") {
    if (
      arguments_.filter((item) => item === "--json").length > 1 ||
      arguments_.slice(1).some((value) => value !== "--json")
    ) {
      return usage();
    }
    const connected = await connectRuntime(environment, {
      readerTimeoutMs: 15_000,
    });
    if (!connected.ok) {
      const document = {
        schema_version: 2,
        product: PRODUCT,
        state: "unavailable",
        code: connected.code,
      };
      emit(
        asJson
          ? `${JSON.stringify(document)}\n`
          : `用户画像读取失败：${document.code}\n`,
      );
      return connected.exitCode;
    }
    const outcome = await connected.reader<unknown>("/api/v2/user-profile");
    const value =
      outcome.state === "ok"
        ? (outcome.data as {
            request_id?: unknown;
            profile?: unknown;
            elapsed_ms?: unknown;
          })
        : null;
    const valid =
      value !== null &&
      typeof value.request_id === "string" &&
      (value.profile === null ||
        (typeof value.profile === "object" && !Array.isArray(value.profile))) &&
      typeof value.elapsed_ms === "number";
    if (!valid) {
      const document = {
        schema_version: 2,
        product: PRODUCT,
        state: outcome.state === "ok" ? "invalid" : outcome.state,
        code: outcome.state === "ok" ? "response_invalid" : outcome.code,
      };
      emit(
        asJson
          ? `${JSON.stringify(document)}\n`
          : `用户画像读取失败：${document.code}\n`,
      );
      return EXIT_FAILED;
    }
    const document = {
      schema_version: 2,
      product: PRODUCT,
      state: "ok",
      ...value,
    };
    const content =
      value.profile && typeof value.profile === "object"
        ? (value.profile as { content?: unknown }).content
        : null;
    emit(
      asJson
        ? `${JSON.stringify(document)}\n`
        : value.profile === null
          ? "尚未形成用户长期画像。\n"
          : `${typeof content === "string" ? content : JSON.stringify(value.profile)}\n`,
    );
    return EXIT_OK;
  }
  if (named === "memories") {
    const parsed = await parseMemoryCommand(arguments_.slice(1));
    if (!parsed) return usage();
    // Local inputs first: an unreadable file changes nothing and is a usage
    // error, before the Runtime is even contacted.
    let text: string | null = null;
    if (parsed.kind === "update") {
      text = await readMemoryContentFile(parsed.contentFile);
      if (text === null) return usage();
    }
    if (parsed.kind === "add") {
      text =
        parsed.text ??
        (parsed.contentFile === undefined
          ? null
          : await readMemoryContentFile(parsed.contentFile));
      if (text === null || !validMemoryText(text)) return usage();
    }
    if (parsed.kind === "delete" && !parsed.confirmed) {
      const namespace = namespaceFlags(parsed.namespace);
      return emitConfirmPreview(asJson, {
        command: "memories delete",
        schemaVersion: 2,
        target: parsed.memoryId,
        effect: `物理删除记忆 ${parsed.memoryId}，删除后无法恢复`,
        retry: `${PRODUCT} memories delete ${parsed.memoryId}${namespace} --yes`,
      });
    }
    const connected = await connectRuntime(environment, {
      readerTimeoutMs: 15_000,
    });
    if (!connected.ok) {
      const document = {
        schema_version: 2,
        product: PRODUCT,
        command: `memories ${parsed.kind}`,
        // A write whose Runtime is absent has an unknown outcome to report;
        // a read simply was not checked.
        state:
          parsed.kind === "add" ||
          parsed.kind === "update" ||
          parsed.kind === "delete"
            ? "unknown"
            : connected.exitCode === EXIT_RUNTIME_STOPPED
              ? "not_checked"
              : "unavailable",
        code: connected.code,
      };
      emit(
        asJson
          ? `${JSON.stringify(document)}\n`
          : `记忆操作未完成：${document.state}（${document.code}）\n`,
      );
      return connected.exitCode;
    }
    const { reader, operations } = connected;
    if (parsed.kind === "list") {
      const document = await readMemoryPage(reader, parsed.query);
      emit(
        asJson
          ? `${JSON.stringify(document)}\n`
          : renderMemoryPageText(document),
      );
      return document.page.state === "ok" ? EXIT_OK : EXIT_FAILED;
    }
    if (parsed.kind === "show") {
      const document = await readMemoryDetail(
        reader,
        parsed.memoryId,
        parsed.namespace,
      );
      emit(
        asJson
          ? `${JSON.stringify(document)}\n`
          : renderMemoryDetailText(document),
      );
      return document.memory.state === "ok" ? EXIT_OK : EXIT_FAILED;
    }
    if (parsed.kind === "add" || parsed.kind === "add-status") {
      const document =
        parsed.kind === "add"
          ? await addMemory(operations, text ?? "", parsed.requestId)
          : await readMemoryAddStatus(reader, parsed.requestId);
      emit(
        asJson
          ? `${JSON.stringify(document)}\n`
          : renderMemoryAddText(document),
      );
      return memoryAddExitCode(document);
    }
    const memoryPath = `/api/v2/memories/${encodeURIComponent(parsed.memoryId)}`;
    const outcome =
      parsed.kind === "update"
        ? await operations.put<unknown>(memoryPath, {
            content: text,
            ...namespaceParameters(parsed.namespace),
            ...(parsed.task === undefined ? {} : { task: parsed.task }),
          })
        : await operations.delete<unknown>(
            withQuery(memoryPath, namespaceParameters(parsed.namespace)),
          );
    const document = {
      schema_version: 2,
      product: PRODUCT,
      command: `memories ${parsed.kind}`,
      state: outcome.state,
      operation: parsed.kind,
      memory_id: parsed.memoryId,
      ...(outcome.state === "committed"
        ? parsed.kind === "update"
          ? { result: outcome.data }
          : {}
        : { code: outcome.code }),
    };
    emit(
      asJson
        ? `${JSON.stringify(document)}\n`
        : outcome.state === "committed"
          ? `已${parsed.kind === "update" ? "更新" : "删除"}记忆 ${parsed.memoryId}\n`
          : outcome.code === "memory_id_ambiguous"
            ? "这个 ID 同时存在于多个场景；请加 --kind（以及 --variant）指明是哪一条。\n"
            : `操作失败：${outcome.code}\n`,
    );
    return outcome.state === "committed" ? EXIT_OK : EXIT_FAILED;
  }
  if (named === "graph-width") {
    const parsed = parseGraphWidthCommand(arguments_.slice(1));
    if (!parsed) return usage();
    if (graphWidthDecisionIsDestructive(parsed.decision) && !parsed.confirmed) {
      return emitConfirmPreview(asJson, {
        command: "graph-width",
        target: parsed.decision,
        effect:
          "先导出现有图谱，再归档旧图谱并按新的嵌入维度重建；归档后旧图谱不再使用",
        retry: `${PRODUCT} graph-width --decision ${parsed.decision} --yes`,
      });
    }
    const connected = await connectRuntime(environment);
    if (!connected.ok) {
      emit(
        asJson
          ? `${JSON.stringify({ schema_version: 1, product: PRODUCT, command: "graph-width", state: "unknown", decision: parsed.decision, code: connected.code })}\n`
          : `图谱宽度决定未完成：${connected.code}\n`,
      );
      return connected.exitCode;
    }
    const document = await decideGraphWidth(
      parsed.decision,
      connected.operations,
    );
    emit(
      asJson ? `${JSON.stringify(document)}\n` : renderGraphWidthText(document),
    );
    return graphWidthExitCode(document);
  }
  if (command === "service-plan") {
    const options = parseServiceOptions(arguments_.slice(1), false);
    if (!options) return usage();
    const platform = options.values.get("platform") ?? process.platform;
    if (platform !== "darwin" && platform !== "linux" && platform !== "win32") {
      return usage();
    }
    const home = options.values.get("home") ?? os.homedir();
    const installRoot = options.values.get("install-root");
    if (!installRoot) return usage();
    const dataDir = serviceDataDirectory(
      options.values.get("data-dir"),
      environment,
      platform,
    );
    if (dataDir === null) return usage();
    const uidText = options.values.get("uid");
    const uid = uidText === undefined ? process.getuid?.() : Number(uidText);
    const plan = planUserService(
      runtimeInstallLayout({
        platform,
        home,
        installRoot: canonicalManagedServiceRoot(platform, installRoot),
        dataDir,
        ...hourlyIsolationFromEnvironment(environment),
      }),
      { uid, xdgConfigHome: options.values.get("xdg-config-home") },
    );
    process.stdout.write(`${JSON.stringify(plan, null, 2)}\n`);
    return plan.status === "ready" ? 0 : 4;
  }
  if (command === "service-install") {
    const options = parseServiceOptions(arguments_.slice(1), true);
    if (!options) return usage();
    if (packagedAppCliPath(process.argv[1], environment)) {
      process.stdout.write(
        `${JSON.stringify(
          {
            schema_version: 1,
            product: PRODUCT,
            command: "service-install",
            state: "managed_by_app",
            code: "runtime_managed_by_memhub_app",
            apply: false,
            message: "后台已由 MemHub App 管理，不会安装第二套服务。",
          },
          null,
          2,
        )}\n`,
      );
      return EXIT_OK;
    }
    const platform = options.values.get("platform") ?? process.platform;
    if (platform !== "darwin" && platform !== "linux" && platform !== "win32") {
      return usage();
    }
    if (localExperienceMode) {
      return emitLocalExperienceUnavailable(
        "service-install",
        options.confirmed,
        asJson,
      );
    }
    const home = options.values.get("home") ?? os.homedir();
    const installRoot = options.values.get("install-root");
    if (!installRoot) return usage();
    const dataDir = serviceDataDirectory(
      options.values.get("data-dir"),
      environment,
      platform,
    );
    if (dataDir === null) return usage();
    const uidText = options.values.get("uid");
    const uid = uidText === undefined ? process.getuid?.() : Number(uidText);
    const layout = runtimeInstallLayout({
      platform,
      home,
      installRoot: canonicalManagedServiceRoot(platform, installRoot),
      dataDir,
      ...hourlyIsolationFromEnvironment(environment),
    });
    const plan = planUserService(layout, {
      uid,
      xdgConfigHome: options.values.get("xdg-config-home"),
    });
    if (!options.confirmed) {
      process.stdout.write(
        `${JSON.stringify({ ...plan, apply: false }, null, 2)}\n`,
      );
      return plan.status === "ready" ? 0 : 4;
    }
    const serviceOptions = {
      uid,
      xdgConfigHome: options.values.get("xdg-config-home"),
    };
    const applied = await prepareServiceInstall(layout, serviceOptions)
      .then((prepared) => applyPreparedServiceInstall(prepared))
      .then(async (plan) => {
        // Registration is not activation. `launchctl bootstrap`/`systemctl
        // enable --now` can return success while the managed Runtime is still
        // starting or has already failed. Require the same verified identity
        // probe used by start/restart/install/upgrade before reporting success.
        const ready = await (
          controls.waitForRuntimeReady ?? waitForRuntimeReady
        )(layout.dataDir);
        if (!ready) throw new Error("runtime_service_activation_unverified");
        return plan;
      })
      .catch(async (error: unknown) => {
        const migration =
          error instanceof Error &&
          error.message === "runtime_service_activation_unverified"
            ? await runtimeMigrationBlock(layout.dataDir, "service-install")
            : null;
        process.stderr.write(
          `${JSON.stringify(
            migration ?? serviceErrorDocument("service-install", error),
            null,
            2,
          )}\n`,
        );
        return null;
      });
    if (applied === null) return EXIT_FAILED;
    process.stdout.write(
      `${JSON.stringify({ ...applied, apply: true }, null, 2)}\n`,
    );
    return 0;
  }
  if (command === "service-uninstall") {
    const options = parseServiceOptions(arguments_.slice(1), true);
    if (!options) return usage();
    const platform = options.values.get("platform") ?? process.platform;
    if (platform !== "darwin" && platform !== "linux" && platform !== "win32") {
      return usage();
    }
    const home = options.values.get("home") ?? os.homedir();
    const installRoot = options.values.get("install-root");
    if (!installRoot) return usage();
    const dataDir = serviceDataDirectory(
      options.values.get("data-dir"),
      environment,
      platform,
    );
    if (dataDir === null) return usage();
    const uidText = options.values.get("uid");
    const uid = uidText === undefined ? process.getuid?.() : Number(uidText);
    const layout = runtimeInstallLayout({
      platform,
      home,
      installRoot: canonicalManagedServiceRoot(platform, installRoot),
      dataDir,
      ...hourlyIsolationFromEnvironment(environment),
    });
    const plan = planUserService(layout, {
      uid,
      xdgConfigHome: options.values.get("xdg-config-home"),
    });
    if (!options.confirmed) {
      process.stdout.write(
        `${JSON.stringify({ ...plan, apply: false }, null, 2)}\n`,
      );
      return plan.status === "ready" ? 0 : 4;
    }
    const result = await uninstallServicePreservingData(layout, {
      uid,
      xdgConfigHome: options.values.get("xdg-config-home"),
      // `memhub stop` already booted the label out; booting it out again is the
      // desired end state, not a failure. Without this the plist would be left
      // behind and block the next install.
      ...(layout.platform === "darwin"
        ? {
            runCommand: createTolerantLaunchctlRunner(runLaunchctlCaptured),
          }
        : {}),
    }).catch((error: unknown) => {
      // Mirror install/upgrade: a refused uninstall must be a structured
      // error, not a bare throw, so the caller can tell a protected-file
      // refusal from a crash and gets a next step.
      process.stderr.write(
        `${JSON.stringify(
          serviceErrorDocument("service-uninstall", error),
          null,
          2,
        )}\n`,
      );
      return null;
    });
    if (result === null) return EXIT_FAILED;
    await removeRuntimeServiceOwner(layout.dataDir).catch(() => undefined);
    process.stdout.write(
      `${JSON.stringify({ ...plan, apply: true, result }, null, 2)}\n`,
    );
    return 0;
  }
  if (command === "install") {
    const parsed = parseInstallOptions(
      arguments_.slice(1),
      environment,
      process.argv[1],
    );
    if (!parsed) return usage();
    const appCli = packagedAppCliPath(process.argv[1], environment);
    if (appCli) {
      const binDirectory = path.resolve(
        parsed.binDirectory ?? path.join(parsed.home, ".local", "bin"),
      );
      const baseLink = {
        schema_version: 1,
        platform: "darwin",
        home: path.resolve(parsed.home),
        install_root: "/Applications/MemHub.app",
        bin_directory: binDirectory,
        status: "create",
        blocked_reason: null,
        link_path: path.join(binDirectory, "memhub"),
        target_path: appCli,
      } as const;
      const links = await Promise.all(
        OBJECTIVE_CLI_ALIASES.map((name) => planManagedCliLink(baseLink, name)),
      );
      const blocked = links.find((link) => link.status === "blocked");
      if (!parsed.confirmed) {
        process.stdout.write(
          `${JSON.stringify({ cli: links, apply: false }, null, 2)}\n`,
        );
        return blocked ? EXIT_PLAN_BLOCKED : EXIT_OK;
      }
      if (blocked) return EXIT_PLAN_BLOCKED;
      const receipts: Awaited<ReturnType<typeof installManagedCliLink>>[] = [];
      try {
        for (const link of links) {
          receipts.push(await installManagedCliLink(link));
        }
        process.stdout.write(
          `${JSON.stringify({ cli: receipts, apply: true }, null, 2)}\n`,
        );
        return EXIT_OK;
      } catch (error) {
        for (const receipt of receipts.reverse()) {
          await rollbackManagedCliLink(receipt).catch(() => undefined);
        }
        process.stderr.write(
          `${JSON.stringify(serviceErrorDocument("install", error), null, 2)}\n`,
        );
        return EXIT_FAILED;
      }
    }
    if (localExperienceMode) {
      const expectedInstallRoot = defaultInstallRoot(
        process.platform,
        parsed.home,
        environment.LOCALAPPDATA,
        hourlyIsolationFromEnvironment(environment).hourlyPackageId,
      );
      if (
        path.resolve(parsed.installRoot) !== path.resolve(expectedInstallRoot)
      ) {
        return emitLocalExperienceUnavailable(
          "install",
          parsed.confirmed,
          asJson,
        );
      }
      const plan = await planLocalInstallOrError(
        "install",
        asJson,
        parsed.source,
        parsed.installRoot,
        parsed.allowDowngrade,
      );
      if (plan === null) return EXIT_FAILED;
      const unavailable = {
        status: "blocked",
        code: "local_experience_operation_unavailable",
        reason: "体验版暂不支持",
      };
      if (!parsed.confirmed) {
        process.stdout.write(
          `${JSON.stringify({ distribution: plan, cli: unavailable, cli_aliases: unavailable, memhub_cli: unavailable, shell_path: unavailable, service: unavailable, apply: false }, null, 2)}\n`,
        );
        return EXIT_OK;
      }
      const receipt = await applyLocalInstall(plan).catch((error: unknown) => {
        process.stderr.write(
          `${JSON.stringify(serviceErrorDocument("install", error), null, 2)}\n`,
        );
        return null;
      });
      if (receipt === null) return EXIT_FAILED;
      process.stdout.write(
        `${JSON.stringify({ distribution: receipt, cli: unavailable, cli_aliases: unavailable, memhub_cli: unavailable, shell_path: unavailable, service: unavailable, apply: true }, null, 2)}\n`,
      );
      return EXIT_OK;
    }
    // Only persist the receipt when the home is explicitly provided. Tests and
    // embedders pass `{}` with no `--home`; writing to the ambient
    // `os.homedir()` there would touch the developer's real `~/.one-memory`.
    const explicitHome =
      environment.HOME !== undefined || arguments_.includes("--home");
    const sourceSelection = await selectInstallSource(
      parsed,
      environment,
    ).catch((error: unknown) => {
      process.stderr.write(
        `${JSON.stringify(serviceErrorDocument("install", error), null, 2)}\n`,
      );
      return null;
    });
    if (sourceSelection === null) return EXIT_FAILED;
    if (sourceSelection.kind === "remote" && !parsed.confirmed) {
      const resolved = await resolveRemoteInstallPreview(
        sourceSelection,
        controls,
      ).catch((error: unknown) => {
        process.stderr.write(
          `${JSON.stringify(serviceErrorDocument("install", error), null, 2)}\n`,
        );
        return null;
      });
      if (resolved === null) return EXIT_FAILED;
      process.stdout.write(
        `${JSON.stringify({ release: remoteReleasePlan(resolved), install_root: parsed.installRoot, data_dir: parsed.dataDir ?? path.join(parsed.home, ".one-memory"), data_preserved: true, apply: false }, null, 2)}\n`,
      );
      return EXIT_OK;
    }
    await using acquired =
      sourceSelection.kind === "remote"
        ? await acquireRemoteInstallSource(sourceSelection, controls).catch(
            (error: unknown) => {
              process.stderr.write(
                `${JSON.stringify(serviceErrorDocument("install", error), null, 2)}\n`,
              );
              return null;
            },
          )
        : null;
    if (sourceSelection.kind === "remote" && acquired === null) {
      return EXIT_FAILED;
    }
    const installSource =
      sourceSelection.kind === "local"
        ? sourceSelection.source
        : acquired!.distribution;
    const plan = await planLocalInstallOrError(
      "install",
      asJson,
      installSource,
      parsed.installRoot,
      parsed.allowDowngrade,
    );
    if (plan === null) return EXIT_FAILED;
    const layout = runtimeInstallLayout({
      platform: plan.platform,
      home: parsed.home,
      installRoot: canonicalManagedServiceRoot(
        plan.platform,
        plan.current_link,
      ),
      dataDir: parsed.dataDir,
      ...hourlyIsolationFromEnvironment(environment),
    });
    const cliLink = await planCliLink({
      home: parsed.home,
      installRoot: plan.install_root,
      binDirectory: parsed.binDirectory,
      platform: plan.platform,
    });
    const companionLinks = await planCliCompanionLinks(cliLink);
    const memhubLink =
      companionLinks.find(
        (link) => path.posix.basename(link.link_path) === "memhub",
      ) ?? null;
    const blockedCompanion = companionLinks.find(
      (link) => link.status === "blocked",
    );
    // "装完即短指令可用": make the managed bin directory reachable from a new
    // shell. This is part of the same preview/apply transaction as the links
    // and service, so a user sees it before `--yes` and it rolls back with the
    // rest on failure.
    const shellPath = await planShellPath({
      binDirectory: cliLink.bin_directory,
      home: parsed.home,
      platform: plan.platform,
      // `--no-shell-path` keeps memhub out of the login profile entirely;
      // `--shell-profile` redirects the block to a caller-named file so an
      // isolated install never edits the real `~/.zprofile`/`~/.profile`.
      // An explicit `--home` (or a HOME that is not the real account home)
      // marks an isolated/sandboxed install: writing the login profile there
      // would touch host configuration the caller never asked us to modify, so
      // the block is skipped unless the caller names a profile explicitly.
      ...(parsed.shellPathDisabled
        ? { disabled: true }
        : parsed.shellProfile
          ? { profilePath: parsed.shellProfile }
          : isIsolatedHome(parsed.home)
            ? { disabled: true }
            : {}),
    });
    const serviceOptions = {
      uid: parsed.uid,
      xdgConfigHome: parsed.xdgConfigHome,
    };
    const service = planUserService(layout, serviceOptions);
    if (!parsed.confirmed) {
      process.stdout.write(
        `${JSON.stringify({ distribution: plan, cli: cliLink, cli_aliases: companionLinks, memhub_cli: memhubLink, shell_path: shellPath, service, apply: false }, null, 2)}\n`,
      );
      return service.status === "ready" &&
        cliLink.status !== "blocked" &&
        blockedCompanion === undefined &&
        shellPath.status !== "blocked"
        ? 0
        : 4;
    }
    if (
      service.status !== "ready" ||
      cliLink.status === "blocked" ||
      blockedCompanion !== undefined ||
      shellPath.status === "blocked"
    )
      return 4;
    const preparedService = await prepareServiceInstall(
      layout,
      serviceOptions,
    ).catch((error: unknown) => {
      process.stderr.write(
        `${JSON.stringify(serviceErrorDocument("install", error), null, 2)}\n`,
      );
      return null;
    });
    if (preparedService === null) return EXIT_FAILED;
    // `applyLocalInstall` re-verifies the plan and can throw at apply time
    // (unmanaged `current`, source drift). Report that as the structured
    // envelope, not a bare thrown string.
    const receipt = await applyLocalInstall(plan).catch((error: unknown) => {
      process.stderr.write(
        `${JSON.stringify(serviceErrorDocument("install", error), null, 2)}\n`,
      );
      return null;
    });
    if (receipt === null) return EXIT_FAILED;
    let cliReceipt: Awaited<ReturnType<typeof installCliLink>> | null = null;
    const companionReceipts: Array<
      Awaited<ReturnType<typeof installManagedCliLink>>
    > = [];
    let shellPathReceipt: Awaited<ReturnType<typeof applyShellPath>> | null =
      null;
    let installReceiptWritten = false;
    let serviceApplied = false;
    let migrated = false;
    // `null` until the migration is handed this home; "failed" if it threw.
    let migrationState: SdkMigrationResult["state"] | "failed" | null = null;
    let deferredCode: string | null = null;
    let deferredRestored = false;
    let previousOwner:
      | Awaited<ReturnType<typeof claimCliServiceOwnership>>
      | undefined;
    try {
      cliReceipt = await installCliLink(cliLink);
      for (const link of companionLinks) {
        companionReceipts.push(await installManagedCliLink(link));
      }
      shellPathReceipt = await applyShellPath(shellPath);
      previousOwner = await claimCliServiceOwnership(layout.dataDir);
      // `install` over an existing home (reinstall, App-to-CLI takeover) must
      // migrate it too, before the new Runtime starts serving it.
      migrationState = "failed";
      const migration = await (
        controls.migrateSdkData ?? migrateSdkDataBeforeStart
      )({
        coreCommand: layout.coreCommand,
        dataDir: layout.dataDir,
        runtimeBuild: String(plan.build_number ?? plan.version),
        asJson: true,
        caller: "cli-install",
        sourceRuntime: await readInstalledRuntimeSource(
          plan.install_root,
          receipt.previous_current_target,
        ),
      });
      migrationState = migration.state;
      // A deferred migration keeps this install: the new Runtime service is
      // registered and its migration gate keeps Core down until the home is
      // migrated. Rolling back would hand the home to the previous (App or CLI)
      // Runtime, which has no gate and would serve the legacy data.
      if (migration.state === "deferred") {
        deferredCode = migration.code ?? "sdk_migration_incomplete";
        deferredRestored = migration.dataRestored === true;
      }
      migrated =
        migration.state === "cutover" || migration.state === "complete";
      // From here the previous LaunchAgent is never re-bootstrapped: it may
      // be an older Runtime with no migration gate.
      await applyPreparedServiceInstall({
        ...preparedService,
        restorePreviousOnFailure: false,
      });
      serviceApplied = true;
      // The receipt exists so a later command can find the data dir without
      // flags. An isolated install named its data dir explicitly, and writing
      // `<home>/.one-memory` would add a product entry to a HOME the caller
      // does not own (the C7 matrix flags exactly that). Keep it for real
      // installs where home is the account home.
      if (explicitHome && !isIsolatedHome(parsed.home)) {
        await writeInstallReceipt({
          path: defaultInstallReceiptPath(parsed.home),
          dataDir: layout.dataDir,
          installRoot: plan.install_root,
          home: parsed.home,
        });
        installReceiptWritten = true;
      }
      if (deferredCode !== null) throw new SdkMigrationDeferred(deferredCode);
      // Registering the service is not the same as a working install: on a host
      // where Core cold-start stalls, launchd reports a pid while status is
      // still `runtime_service_running`. Gate on real readiness (same predicate
      // as start/restart/upgrade); failure rolls the whole transaction back.
      const installReady = await (
        controls.waitForRuntimeReady ?? waitForRuntimeReady
      )(layout.dataDir);
      if (!installReady) {
        throw new Error("install_runtime_not_ready");
      }
    } catch (error) {
      // Reverse order for filesystem changes. The service itself is restored
      // last: an App-owned service may have existed before this CLI takeover,
      // and it must restart only after `current` and its ownership document
      // point back to the original App Runtime.
      const rollbackFilesAndOwner = async () => {
        if (shellPathReceipt?.created) {
          await removeShellPath(shellPath).catch(() => undefined);
        }
        if (installReceiptWritten) {
          await removeInstallReceipt(
            defaultInstallReceiptPath(parsed.home),
          ).catch(() => undefined);
        }
        for (const receipt of companionReceipts.reverse()) {
          await rollbackManagedCliLink(receipt);
        }
        if (cliReceipt) await rollbackCliLink(cliReceipt);
        await rollbackLocalInstall(receipt);
        if (previousOwner !== undefined) {
          await restoreServiceOwnership(layout.dataDir, previousOwner).catch(
            () => undefined,
          );
        }
      };
      // Once the migration has been handed this home nothing rolls back: the
      // previous version (App or CLI) may be an older Runtime without the
      // migration gate, and restoring it could serve a home that is not on SDK
      // authority. This install stays; its service keeps Core down until the
      // home is migrated. Only a failure before the migration ran (the
      // previous Runtime was never stopped and the service never touched) is
      // rolled back below.
      if (migrationState !== null) {
        if (!serviceApplied && !migrated && deferredCode === null) {
          // The migration threw. Hand the data dir to this version's gated
          // service anyway so that the previous Runtime does not come back.
          await applyPreparedServiceInstall({
            ...preparedService,
            restorePreviousOnFailure: false,
          }).catch(() => undefined);
        }
        process.stderr.write(
          `${JSON.stringify(
            deferredCode !== null
              ? sdkMigrationFailure(
                  "install",
                  deferredCode,
                  layout.dataDir,
                  "upgrade",
                  deferredRestored,
                )
              : migrated
                ? notReadyAfterMigration("install")
                : migrationState === "failed"
                  ? sdkMigrationFailure(
                      "install",
                      sdkMigrationErrorCode(error),
                      layout.dataDir,
                    )
                  : notReadyAfterUpgrade("install", error),
            null,
            2,
          )}\n`,
        );
        return EXIT_FAILED;
      }
      if (serviceApplied) {
        await rollbackPreparedServiceInstall(
          preparedService,
          rollbackFilesAndOwner,
        ).catch(() => undefined);
      } else {
        await rollbackFilesAndOwner();
      }
      process.stderr.write(
        `${JSON.stringify(serviceErrorDocument("install", error), null, 2)}\n`,
      );
      return EXIT_FAILED;
    }
    process.stdout.write(
      `${JSON.stringify({ distribution: plan, cli: cliLink, cli_aliases: companionLinks, memhub_cli: memhubLink, shell_path: shellPath, service, apply: true }, null, 2)}\n`,
    );
    return 0;
  }
  if (command === "uninstall") {
    const parsed = parseInstallOptions(
      arguments_.slice(1),
      environment,
      process.argv[1],
    );
    if (!parsed) return usage();
    const appCli = packagedAppCliPath(process.argv[1], environment);
    if (appCli) {
      const binDirectory = path.resolve(
        parsed.binDirectory ?? path.join(parsed.home, ".local", "bin"),
      );
      const baseLink = {
        schema_version: 1,
        platform: "darwin",
        home: path.resolve(parsed.home),
        install_root: "/Applications/MemHub.app",
        bin_directory: binDirectory,
        status: "create",
        blocked_reason: null,
        link_path: path.join(binDirectory, "memhub"),
        target_path: appCli,
      } as const;
      const links = await Promise.all(
        OBJECTIVE_CLI_ALIASES.map((name) => planManagedCliLink(baseLink, name)),
      );
      const blocked = links.find((link) => link.status === "blocked");
      if (!parsed.confirmed) {
        process.stdout.write(
          `${JSON.stringify({ command: "uninstall", state: "preview", cli: links, data_preserved: true, apply: false }, null, 2)}\n`,
        );
        return blocked ? EXIT_PLAN_BLOCKED : EXIT_OK;
      }
      if (blocked) return EXIT_PLAN_BLOCKED;
      const results = [];
      for (const link of links) {
        results.push({
          link_path: link.link_path,
          result: await removeManagedCliLink(link),
        });
      }
      process.stdout.write(
        `${JSON.stringify({ command: "uninstall", state: "cli_links_removed", cli: results, data_preserved: true, apply: true, next_step: "如需卸载 App，请将 MemHub 拖到废纸篓。" }, null, 2)}\n`,
      );
      return EXIT_OK;
    }
    // Narrowed explicitly rather than trusting `process.platform`: the managed
    // layout only exists for these three, and removing files on anything else
    // would be guessing about paths we never wrote.
    const platform = process.platform;
    if (platform !== "darwin" && platform !== "linux" && platform !== "win32") {
      process.stdout.write(
        `${JSON.stringify({ status: "blocked", reason: "platform_unsupported" }, null, 2)}\n`,
      );
      return EXIT_FAILED;
    }
    if (localExperienceMode) {
      return emitLocalExperienceUnavailable(
        "uninstall",
        parsed.confirmed,
        asJson,
      );
    }
    const layout = runtimeInstallLayout({
      platform,
      home: parsed.home,
      installRoot: canonicalManagedServiceRoot(platform, parsed.installRoot),
      dataDir: parsed.dataDir,
      ...hourlyIsolationFromEnvironment(environment),
    });
    const cliLink = await planCliLink({
      home: parsed.home,
      installRoot: parsed.installRoot,
      binDirectory: parsed.binDirectory,
      platform,
    });
    // Uninstall must clean every objective alias that is not the primary link.
    // `applyLocalUninstall` deletes only `plan.cli_link`, so the compat
    // `one-memory` link it created would otherwise dangle on PATH.
    const companionLinks = await planCliRemovalLinks(cliLink);
    const memhubLink =
      companionLinks.find(
        (link) => path.posix.basename(link.link_path) === "memhub",
      ) ?? null;
    const blockedCompanion = companionLinks.find(
      (link) => link.status === "blocked",
    );
    const shellPath = await planShellPath({
      binDirectory: cliLink.bin_directory,
      home: parsed.home,
      platform,
    });
    const service = planUserService(layout, {
      uid: parsed.uid,
      xdgConfigHome: parsed.xdgConfigHome,
    });
    const plan = await planLocalUninstall({
      installRoot: parsed.installRoot,
      home: parsed.home,
      dataDir: parsed.dataDir ?? null,
      cliLinkPath: cliLink.link_path,
      stopsService: service.status === "ready",
      // Without this, a data dir that happens to live under
      // `<install-root>/versions/` made the safety guard refuse uninstall with
      // no in-product way out. The flag is a per-invocation statement of what
      // this caller is protecting; data is still never deleted here.
      ...(parsed.preserveDataDir ? { preserveDataDir: true } : {}),
    });
    if (!parsed.confirmed) {
      process.stdout.write(
        `${JSON.stringify({ ...plan, cli_aliases: companionLinks, memhub_cli: memhubLink, shell_path: shellPath, apply: false }, null, 2)}\n`,
      );
      return blockedCompanion !== undefined ? EXIT_PLAN_BLOCKED : EXIT_OK;
    }
    if (blockedCompanion !== undefined) {
      return EXIT_PLAN_BLOCKED;
    }
    // The service is stopped first: removing files from under a running daemon
    // leaves a service that cannot start and does not say why.
    if (plan.stops_service) {
      await uninstallServicePreservingData(layout, {
        uid: parsed.uid,
        xdgConfigHome: parsed.xdgConfigHome,
        // Same as `service-uninstall`: `stop` may have already booted the
        // label out, and that is the desired end state.
        ...(layout.platform === "darwin"
          ? {
              runCommand: createTolerantLaunchctlRunner(runLaunchctlCaptured),
            }
          : {}),
      });
    }
    const result = await applyLocalUninstall(plan);
    await removeRuntimeServiceOwner(layout.dataDir).catch(() => undefined);
    // Runtime-control removes its primary link (`cli_link`) but, in its
    // `01eedd8` shape, leaves the `one-memory` compat link behind. Remove it
    // here so uninstall does not leave a dangling link; when the package does
    // not declare a compat link, the CLI removes its own companion instead.
    // Remove every objective alias the CLI may have created; runtime-control's
    // own link(s) were already removed by `applyLocalUninstall`. A not-installed
    // alias is not an error — installs predating `memhub-cli` never had it.
    const companionResults = await Promise.all(
      companionLinks.map(async (link) => ({
        link_path: link.link_path,
        result: await removeManagedCliLink(link).catch(() => "not-installed"),
      })),
    );
    // The profile block is ours to remove; data is never touched here.
    const shellPathResult = await removeShellPath(shellPath);
    const installReceiptResult = await removeInstallReceipt(
      defaultInstallReceiptPath(parsed.home),
    );
    process.stdout.write(
      `${JSON.stringify({ ...plan, cli_aliases: companionLinks, memhub_cli: memhubLink, shell_path: shellPath, apply: true, result, cli_alias_results: companionResults, shell_path_result: shellPathResult, install_receipt_result: installReceiptResult }, null, 2)}\n`,
    );
    return 0;
  }
  if (command === "delete-data") {
    const parsed = parseDataDeletionCommand(arguments_.slice(1));
    if (!parsed) return usage();
    const config = resolveRuntimeCliConfig(environment, process.argv[1]);
    const dataDir = parsed.dataDir ?? config.dataDir;
    const status = await inspectRuntimeStatus({
      dataDir,
      probeIdentity,
      requireServiceReceipt: true,
    });
    const plan = await planDataDeletion({
      dataDir,
      home: environment.HOME ?? os.homedir(),
      runtimeRunning: status.state === "running",
    });
    if (!parsed.confirmed) {
      // The preview names the exact directory; `--yes` deletes it.
      process.stdout.write(
        `${JSON.stringify(
          {
            ...plan,
            apply: false,
            ...(plan.state === "preview"
              ? {
                  next_step: `确认要永久删除这个数据目录后运行（--confirm 必须再写一遍同一个目录）：${PRODUCT} delete-data --data-dir ${dataDir} --yes --confirm ${dataDir}`,
                }
              : {}),
          },
          null,
          2,
        )}\n`,
      );
      return plan.state === "preview" ? EXIT_OK : EXIT_PLAN_BLOCKED;
    }
    if (plan.state !== "preview") {
      process.stdout.write(
        `${JSON.stringify({ ...plan, apply: false }, null, 2)}\n`,
      );
      return EXIT_PLAN_BLOCKED;
    }
    if (parsed.confirmPath === undefined) {
      // Deleting every memory is the one action with no way back: `--yes`
      // alone is not enough, the directory has to be typed a second time.
      process.stdout.write(
        `${JSON.stringify(
          {
            ...plan,
            state: "blocked",
            apply: false,
            code: "data_deletion_confirmation_required",
            next_step: `删除需要再写一遍数据目录：${PRODUCT} delete-data --data-dir ${plan.data_dir} --yes --confirm ${plan.data_dir}`,
          },
          null,
          2,
        )}\n`,
      );
      return EXIT_PLAN_BLOCKED;
    }
    try {
      // `--confirm <path>` must name this exact directory.
      const receipt = await applyDataDeletion(plan, parsed.confirmPath);
      await removeInstallReceipt(
        defaultInstallReceiptPath(environment.HOME ?? os.homedir()),
      );
      process.stdout.write(
        `${JSON.stringify({ ...receipt, apply: true }, null, 2)}\n`,
      );
      return EXIT_OK;
    } catch (error) {
      process.stdout.write(
        `${JSON.stringify(
          {
            schema_version: 1,
            product: PRODUCT,
            command: "delete-data",
            status: "error",
            code:
              error instanceof Error ? error.message : "data_deletion_failed",
            exit_code: EXIT_FAILED,
            next_step: "未完成删除；请确认目标目录后重试。",
          },
          null,
          2,
        )}\n`,
      );
      return EXIT_FAILED;
    }
  }
  if (command === "upgrade") {
    const parsed = parseInstallOptions(
      arguments_.slice(1),
      environment,
      process.argv[1],
    );
    if (!parsed) return usage();
    const explicitHome =
      environment.HOME !== undefined || arguments_.includes("--home");
    const sourceSelection = await selectInstallSource(
      parsed,
      environment,
    ).catch((error: unknown) => {
      process.stderr.write(
        `${JSON.stringify(serviceErrorDocument("upgrade", error), null, 2)}\n`,
      );
      return null;
    });
    if (sourceSelection === null) return EXIT_FAILED;
    if (sourceSelection.kind === "remote" && !parsed.confirmed) {
      const resolved = await resolveRemoteInstallPreview(
        sourceSelection,
        controls,
      ).catch((error: unknown) => {
        process.stderr.write(
          `${JSON.stringify(serviceErrorDocument("upgrade", error), null, 2)}\n`,
        );
        return null;
      });
      if (resolved === null) return EXIT_FAILED;
      process.stdout.write(
        `${JSON.stringify({ release: remoteReleasePlan(resolved), install_root: parsed.installRoot, data_dir: parsed.dataDir ?? path.join(parsed.home, ".one-memory"), data_preserved: true, apply: false }, null, 2)}\n`,
      );
      return EXIT_OK;
    }
    await using acquired =
      sourceSelection.kind === "remote"
        ? await acquireRemoteInstallSource(sourceSelection, controls).catch(
            (error: unknown) => {
              process.stderr.write(
                `${JSON.stringify(serviceErrorDocument("upgrade", error), null, 2)}\n`,
              );
              return null;
            },
          )
        : null;
    if (sourceSelection.kind === "remote" && acquired === null) {
      return EXIT_FAILED;
    }
    const installSource =
      sourceSelection.kind === "local"
        ? sourceSelection.source
        : acquired!.distribution;
    const plan = await planLocalInstallOrError(
      "upgrade",
      asJson,
      installSource,
      parsed.installRoot,
      parsed.allowDowngrade,
    );
    if (plan === null) return EXIT_FAILED;
    const layout = runtimeInstallLayout({
      platform: plan.platform,
      home: parsed.home,
      installRoot: canonicalManagedServiceRoot(
        plan.platform,
        plan.current_link,
      ),
      dataDir: parsed.dataDir,
      ...hourlyIsolationFromEnvironment(environment),
    });
    const cliLink = await planCliLink({
      home: parsed.home,
      installRoot: plan.install_root,
      binDirectory: parsed.binDirectory,
      platform: plan.platform,
    });
    const companionLinks = await planCliCompanionLinks(cliLink);
    const memhubLink =
      companionLinks.find(
        (link) => path.posix.basename(link.link_path) === "memhub",
      ) ?? null;
    const blockedCompanion = companionLinks.find(
      (link) => link.status === "blocked",
    );
    const serviceOptions = {
      uid: parsed.uid,
      xdgConfigHome: parsed.xdgConfigHome,
    };
    const service = planUserService(layout, serviceOptions);
    if (!parsed.confirmed) {
      process.stdout.write(
        `${JSON.stringify({ distribution: plan, cli: cliLink, cli_aliases: companionLinks, memhub_cli: memhubLink, service, apply: false }, null, 2)}\n`,
      );
      return service.status === "ready" &&
        cliLink.status === "present" &&
        blockedCompanion === undefined
        ? 0
        : 4;
    }
    if (
      service.status !== "ready" ||
      cliLink.status !== "present" ||
      blockedCompanion !== undefined
    )
      return 4;
    const receipt = await applyLocalInstall(plan).catch((error: unknown) => {
      process.stderr.write(
        `${JSON.stringify(serviceErrorDocument("upgrade", error), null, 2)}\n`,
      );
      return null;
    });
    if (receipt === null) return EXIT_FAILED;
    const memhubReceipts: Array<
      Awaited<ReturnType<typeof installManagedCliLink>>
    > = [];
    let previousOwner:
      | Awaited<ReturnType<typeof claimCliServiceOwnership>>
      | undefined;
    let migrated = false;
    // `null` until the migration is handed this home; "failed" if it threw.
    let migrationState: SdkMigrationResult["state"] | "failed" | null = null;
    /**
     * Restore the previous version's files and ownership. Only used for a
     * failure before the migration ran: the previous Runtime was never stopped
     * and the service never restarted, so nothing new starts serving. After
     * the migration has been handed the home the upgrade never rolls back:
     * the previous Runtime may predate the migration gate and would serve a
     * home that is not on SDK authority.
     */
    const rollbackUpgrade = async (): Promise<void> => {
      for (const receipt of memhubReceipts.reverse()) {
        await rollbackManagedCliLink(receipt);
      }
      await rollbackLocalInstall(receipt);
      if (previousOwner !== undefined) {
        await restoreServiceOwnership(layout.dataDir, previousOwner).catch(
          () => undefined,
        );
      }
    };
    /**
     * After a migration that did not finish, make sure the process holding the
     * data dir is the new version's service (gated: it keeps Core down while
     * the migration is deferred) and not the previous Runtime. The migration
     * fence already stopped it unless the status probe itself failed.
     */
    const keepUpgradedRuntimeOffLegacyHome = async (): Promise<void> => {
      try {
        await restartUserService(layout, serviceOptions);
      } catch {
        await (controls.stopRuntime ?? stopRuntimeProcess)({
          dataDir: parsed.dataDir ?? layout.dataDir,
          probeIdentity,
        }).catch(() => undefined);
      }
    };
    try {
      for (const link of companionLinks) {
        memhubReceipts.push(await installManagedCliLink(link));
      }
      previousOwner = await claimCliServiceOwnership(layout.dataDir);
      migrationState = "failed";
      const migration = await (
        controls.migrateSdkData ?? migrateSdkDataBeforeStart
      )({
        coreCommand: layout.coreCommand,
        dataDir: parsed.dataDir ?? layout.dataDir,
        runtimeBuild: String(plan.build_number ?? plan.version),
        asJson,
        caller: "cli-upgrade",
        sourceRuntime: await readInstalledRuntimeSource(
          plan.install_root,
          receipt.previous_current_target,
        ),
      });
      migrationState = migration.state;
      if (migration.state === "deferred") {
        // Stay on the new version. Its Runtime service refuses to spawn Core
        // while the migration is deferred, so nothing serves the legacy home;
        // rolling back would restart the old Runtime, which has no such gate.
        await keepUpgradedRuntimeOffLegacyHome();
        process.stderr.write(
          `${JSON.stringify(sdkMigrationFailure("upgrade", migration.code ?? "sdk_migration_incomplete", parsed.dataDir ?? layout.dataDir, "upgrade", migration.dataRestored === true), null, 2)}\n`,
        );
        return EXIT_FAILED;
      }
      // From here a failure keeps the new version: an older Runtime either
      // refuses a migrated home or, predating the migration gate, would serve
      // one that is not on SDK authority.
      migrated =
        migration.state === "cutover" || migration.state === "complete";
      await restartUserService(layout, serviceOptions);
      // A restart that returns without error only means launchd/systemd
      // accepted the command. O-22: the first `upgrade` implementation
      // reported success even when the new artifact crash-looped, leaving
      // `current` on a version that never came up. Verify the restarted
      // Runtime actually becomes healthy; if it does not, fail loudly. It is
      // not rolled back (product rule: SDK authority or not serving at all).
      const dataDir = parsed.dataDir ?? layout.dataDir;
      const ready = await (controls.waitForRuntimeReady ?? waitForRuntimeReady)(
        dataDir,
      );
      if (!ready && migrated) {
        process.stderr.write(
          `${JSON.stringify(notReadyAfterMigration("upgrade"), null, 2)}\n`,
        );
        return EXIT_FAILED;
      }
      if (!ready) {
        // Not rolled back: see `rollbackUpgrade`. If the service is blocked on
        // the migration (it re-runs it before spawning Core), say so.
        const blocked = await runtimeMigrationBlock(dataDir, "upgrade");
        process.stderr.write(
          `${JSON.stringify(blocked ?? notReadyAfterUpgrade("upgrade"), null, 2)}\n`,
        );
        return EXIT_FAILED;
      }
      if (explicitHome) {
        await writeInstallReceipt({
          path: defaultInstallReceiptPath(parsed.home),
          dataDir: layout.dataDir,
          installRoot: plan.install_root,
          home: parsed.home,
        });
      }
    } catch (error) {
      if (migrationState === null) {
        await rollbackUpgrade();
        process.stderr.write(
          `${JSON.stringify(serviceErrorDocument("upgrade", error), null, 2)}\n`,
        );
        return EXIT_FAILED;
      }
      if (migrated) {
        // An older Runtime refuses a migrated home: keep the new version.
        process.stderr.write(
          `${JSON.stringify(notReadyAfterMigration("upgrade"), null, 2)}\n`,
        );
        return EXIT_FAILED;
      }
      if (migrationState === "failed") {
        // The migration itself threw: its outcome is unknown, so treat the
        // home as not migrated and keep the old Runtime off it.
        await keepUpgradedRuntimeOffLegacyHome();
        process.stderr.write(
          `${JSON.stringify(sdkMigrationFailure("upgrade", sdkMigrationErrorCode(error), parsed.dataDir ?? layout.dataDir), null, 2)}\n`,
        );
        return EXIT_FAILED;
      }
      // `not_required`: the restart itself failed. Stay on the new version.
      process.stderr.write(
        `${JSON.stringify(notReadyAfterUpgrade("upgrade", error), null, 2)}\n`,
      );
      return EXIT_FAILED;
    }
    process.stdout.write(
      `${JSON.stringify({ distribution: plan, cli: cliLink, cli_aliases: companionLinks, memhub_cli: memhubLink, service, apply: true }, null, 2)}\n`,
    );
    return 0;
  }
  return usage();
}

export function packagedAppCliPath(
  executable: string | undefined,
  environment: NodeJS.ProcessEnv,
): string | null {
  const candidate =
    environment.ONE_MEMORY_TEST_ISOLATED === "1" &&
    environment.ONE_MEMORY_APP_BUNDLE_CLI
      ? environment.ONE_MEMORY_APP_BUNDLE_CLI
      : executable;
  const isolatedAppFixture =
    environment.ONE_MEMORY_TEST_ISOLATED === "1" &&
    environment.ONE_MEMORY_APP_BUNDLE_CLI !== undefined;
  if (!candidate || (process.platform !== "darwin" && !isolatedAppFixture)) {
    return null;
  }
  const resolved = path.resolve(candidate);
  return resolved ===
    "/Applications/MemHub.app/Contents/Resources/cli/one-memory.cjs"
    ? resolved
    : environment.ONE_MEMORY_TEST_ISOLATED === "1" &&
        environment.ONE_MEMORY_APP_BUNDLE_CLI
      ? resolved
      : null;
}

/** Delete a model profile, reporting *why* the server refused when it did.
 *
 * "ok" | "not_found" | "active" | "unavailable" — the middle two are actionable
 * and must not collapse into a generic failure, or the user retries a command
 * that cannot succeed.
 */
async function removeModelProfile(
  dataDir: string,
  profileId: string,
): Promise<"ok" | "not_found" | "active" | "unavailable"> {
  const attachment = await attachToRuntime(dataDir);
  if (attachment === null) return "unavailable";
  try {
    if (await deleteModelProfile(attachment, profileId)) return "ok";
    // 404 (unknown id) and 409 (still active) both arrive here. Ask which,
    // rather than guessing — a wrong message sends the user down the wrong path.
    return await classifyRefusedRemoval(attachment, profileId);
  } catch {
    return "unavailable";
  }
}

/** Distinguish "no such profile" from "profile is still the active one". */
async function classifyRefusedRemoval(
  attachment: { host: string; port: number; apiToken: string },
  profileId: string,
): Promise<"not_found" | "active"> {
  try {
    const response = await axios.get(
      `http://${attachment.host}:${attachment.port}/api/v1/model-profiles`,
      {
        headers: { Authorization: `Bearer ${attachment.apiToken}` },
        timeout: 10_000,
        proxy: false,
        maxRedirects: 0,
        validateStatus: () => true,
      },
    );
    if (response.status === 200) {
      const profiles = (response.data?.profiles ?? []) as Array<{
        profile_id?: string;
        is_active?: boolean;
      }>;
      const match = profiles.find((item) => item.profile_id === profileId);
      return match?.is_active ? "active" : "not_found";
    }
  } catch {
    // fall through
  }
  return "not_found";
}

/** `backup create`: ask Runtime to copy its main database.
 *
 * Only `one-memory.sqlite3` is copied (sources, queues, settings); the SDK
 * memory store under `hy-memory/` is not part of it.
 */
async function applyBackupCreate(
  asJson: boolean,
  descriptor: CommandDescriptor | undefined,
  environment: NodeJS.ProcessEnv,
  parsed: { target: string },
): Promise<number> {
  const attachment = await attachToRuntimeOrNull(environment);
  if (attachment === null) return runtimeUnavailable(descriptor, asJson);
  try {
    const result = await submitBackup(attachment, parsed.target);
    process.stdout.write(
      asJson
        ? `${JSON.stringify({ schema_version: 1, product: PRODUCT, command: "backup", verb: "create", state: "committed", backup: result })}\n`
        : `target: ${result.target}\nbytes: ${result.bytes}\n`,
    );
    return EXIT_OK;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    // Runtime refusing the target (e.g. `backup_target_outside_data_dir`) is a
    // security rejection, not a malformed argument: the flags were fine.
    const code = /^[a-z0-9_.:-]{1,128}$/.test(message)
      ? message
      : "backup_failed";
    const nextStep =
      "备份目标必须是数据目录内的路径；要能用 backup restore 恢复，请放在 <数据目录>/backups/ 下。";
    if (asJson) {
      process.stdout.write(
        `${JSON.stringify({
          schema_version: 1,
          product: PRODUCT,
          command: "backup",
          verb: "create",
          state: "rejected",
          code,
          exit_code: EXIT_FAILED,
          next_step: nextStep,
        })}\n`,
      );
    } else {
      process.stderr.write(`备份失败：${code}\n下一步：${nextStep}\n`);
    }
    return EXIT_FAILED;
  }
}

/** `backup restore`: replace the main database with a managed backup.
 *
 * Contract: docs/contracts/c0-ui-and-restore-contract.md (C0, H25). A restore
 * overwrites data, so without `--yes` it only previews. It needs a *stopped*
 * Runtime; Core refuses it from a runtime-owning process, so the CLI drives a
 * non-owning maintenance Core.
 */
async function applyBackupRestore(
  asJson: boolean,
  environment: NodeJS.ProcessEnv,
  controls: RunControls,
  parsed: { source: string; confirmed: boolean; confirmPath?: string },
): Promise<number> {
  const refuse = (code: string, nextStep: string, exitCode = EXIT_FAILED) => {
    const document = {
      schema_version: 1 as const,
      product: PRODUCT,
      command: "backup",
      verb: "restore" as const,
      state: "rejected" as const,
      code,
      next_step: nextStep,
    };
    emit(
      asJson
        ? `${JSON.stringify(document)}\n`
        : `backup restore 被拒绝：${code}\n下一步：${nextStep}\n`,
    );
    return exitCode;
  };
  if (!restoreConfirmationMatches(parsed.source, parsed.confirmPath)) {
    return refuse(
      "backup_restore_confirmation_mismatch",
      `--confirm 必须与 --source 相同：${PRODUCT} backup restore --source ${parsed.source} --yes --confirm ${parsed.source}`,
    );
  }
  if (!parsed.confirmed) {
    return emitConfirmPreview(asJson, {
      command: "backup restore",
      target: parsed.source,
      effect:
        "用这份受管备份覆盖当前主库（来源、写入队列、配置）；先自动做一份保护性备份。SDK 记忆库 hy-memory/ 不会被替换。需要 Runtime 已停止",
      retry: `${PRODUCT} backup restore --source ${parsed.source} --yes --confirm ${parsed.source}`,
    });
  }
  if (parsed.confirmPath === undefined) {
    // Restoring overwrites the live database: the backup has to be named a
    // second time, the same rule as delete-data.
    return refuse(
      "backup_restore_confirmation_required",
      `恢复需要再写一遍备份路径：${PRODUCT} backup restore --source ${parsed.source} --yes --confirm ${parsed.source}`,
      EXIT_PLAN_BLOCKED,
    );
  }
  const config = resolveRuntimeCliConfig(environment, process.argv[1]);
  const status = await inspectRuntimeStatus({
    dataDir: config.dataDir,
    probeIdentity,
    requireServiceReceipt: true,
  });
  if (status.state === "running") {
    return refuse(
      "backup_restore_runtime_running",
      `先运行 ${PRODUCT} stop 停止 Runtime；恢复会覆盖现有数据，必须在停机时进行。`,
    );
  }
  // A missing runtime receipt is not proof that no Runtime owns the data
  // directory. If the service lease still names a live process, fail closed
  // before starting a maintenance Core.
  const lease = await inspectRuntimeServiceLock(config.dataDir);
  if (lease !== "clear") {
    return refuse(
      "backup_restore_runtime_state_unknown",
      "Runtime 身份无法确认（receipt 缺失但仍有服务锁）。请先运行 memhub stop 并确认进程退出后重试。",
    );
  }
  const runMaintenance =
    controls.runMaintenanceRestore ?? runMaintenanceRestore;
  const restored = await runMaintenance({
    coreCommand: config.coreCommand,
    dataDir: config.dataDir,
    source: parsed.source,
  });
  if (restored.state === "restored") {
    const document = {
      schema_version: 1 as const,
      product: PRODUCT,
      command: "backup",
      verb: "restore" as const,
      state: "restored" as const,
      restored_bytes: restored.result.restoredBytes,
      previous_backup_path: restored.result.previousBackupPath,
      source_schema_version: restored.result.sourceSchemaVersion,
    };
    emit(
      asJson
        ? `${JSON.stringify(document)}\n`
        : `已恢复受管备份：${restored.result.restoredBytes} 字节；保护性备份 ${restored.result.previousBackupPath}\n`,
    );
    return EXIT_OK;
  }
  return refuse(
    restored.code,
    restored.code === "backup_restore_contract_unavailable"
      ? "当前 Core 不提供受管恢复端点；请升级到包含该合同的 Runtime。"
      : "检查来源是否为 <数据目录>/backups/ 中的受管备份后重试。",
    restored.code === "backup_restore_contract_unavailable"
      ? EXIT_PLAN_BLOCKED
      : EXIT_FAILED,
  );
}

/** `scan --source <id>`: ask Runtime to sync one source now. */
async function applyScan(
  asJson: boolean,
  descriptor: CommandDescriptor | undefined,
  environment: NodeJS.ProcessEnv,
  sourceId: string,
): Promise<number> {
  const attachment = await attachToRuntimeOrNull(environment);
  if (attachment === null) return runtimeUnavailable(descriptor, asJson);
  const job = await submitScan(attachment, sourceId).catch((error: unknown) => {
    const message = error instanceof Error ? error.message : "";
    return {
      failed: /^scan_rejected (http_\d+)$/.exec(message)?.[1] ?? "scan_failed",
    };
  });
  if (job !== null && "failed" in job) {
    return emitFailure(asJson, "scan", job.failed);
  }
  if (job === null) {
    // The source cannot be synced — a property of the source, not a failure
    // of the transport. Saying so keeps "not synced" distinct.
    return emitFailure(
      asJson,
      "scan",
      "live_sync_unavailable",
      `来源 ${sourceId} 尚未开启持续同步或不支持增量同步。先运行 memhub sources list 确认来源已连接，再运行 memhub sync resume --source ${sourceId}。`,
    );
  }
  process.stdout.write(
    asJson
      ? `${JSON.stringify({ schema_version: 1, product: PRODUCT, command: "scan", state: "committed", scan: job })}\n`
      : `job_id: ${job.job_id}\nsource_id: ${job.source_id}\nstatus: ${job.status}\n`,
  );
  return EXIT_OK;
}

type SourceConnectCommand = Extract<SourcesCommand, { kind: "ids" | "all" }>;

/**
 * `sources connect`: connect sources directly.
 *
 * - With `--import`: one `/sources/connect` (grant, history import and live
 *   sync); the answer carries the request id that `sources receipt` reads.
 * - Without it: grant each source only (`/sources/{id}/authorize`). Processing
 *   stays frozen: nothing is imported and new sessions are not scanned until
 *   `sync resume` or a later `connect --import`.
 *
 * A source whose location comes from an environment override is only
 * connected with `--confirm-overrides`.
 */
async function connectSources(
  parsed: SourceConnectCommand,
  asJson: boolean,
  environment: NodeJS.ProcessEnv,
): Promise<number> {
  const connected = await connectRuntime(environment);
  if (!connected.ok) {
    emitFlowDocument(
      {
        schema_version: 1,
        product: PRODUCT,
        state: "unknown",
        code: connected.code,
      },
      asJson,
    );
    return connected.exitCode;
  }
  const { reader, operations } = connected;
  if (parsed.kind === "all") {
    const batch = await planBatchImport(reader);
    if (batch.state !== "preview") {
      const document: BatchScanDocument = {
        schema_version: 1,
        product: PRODUCT,
        command: "sources connect",
        state: batch.state === "no_sources" ? "no_sources" : batch.state,
        request_id: null,
        accepted_source_ids: [],
        sources: [],
        not_found_source_ids: batch.notFound,
        code: batch.code,
      };
      emit(
        asJson
          ? `${JSON.stringify(document)}\n`
          : renderBatchScanText(document),
      );
      return batch.state === "no_sources" ? EXIT_OK : EXIT_FAILED;
    }
    if (parsed.importHistory) {
      const document = await executeBatchImport(
        batch.candidates,
        reader,
        operations,
        batch.notFound,
        { confirmOverrides: parsed.confirmOverrides },
      );
      emit(
        asJson
          ? `${JSON.stringify(document)}\n`
          : renderBatchScanText(document),
      );
      return batchScanExitCode(document);
    }
    return await grantSources(
      batch.candidates,
      parsed.confirmOverrides,
      operations,
      asJson,
    );
  }
  if (parsed.importHistory) {
    const document = await connectSourcesWithImport(reader, operations, {
      sourceIds: parsed.sourceIds,
      confirmOverrides: parsed.confirmOverrides,
      ...(parsed.requestId === undefined
        ? {}
        : { requestId: parsed.requestId }),
    });
    emitFlowDocument(document, asJson);
    return flowExitCode(document);
  }
  const outcome = await reader<unknown>("/api/v1/sources");
  const candidates =
    outcome.state === "ok"
      ? validateSourceConnectionCandidates(outcome.data)
      : null;
  if (candidates === null) {
    emitFlowDocument(
      {
        schema_version: 1,
        product: PRODUCT,
        state: outcome.state === "ok" ? "invalid" : "unknown",
        code: outcome.state === "ok" ? "source_response_invalid" : outcome.code,
      },
      asJson,
    );
    return EXIT_FAILED;
  }
  const selected = parsed.sourceIds.map((id) =>
    candidates.find((candidate) => candidate.source_id === id),
  );
  if (selected.some((candidate) => candidate === undefined)) {
    emitFlowDocument(
      {
        schema_version: 1,
        product: PRODUCT,
        state: "invalid",
        code: "source_unavailable",
      },
      asJson,
    );
    return EXIT_FAILED;
  }
  return await grantSources(
    selected as SourceConnectionCandidate[],
    parsed.confirmOverrides,
    operations,
    asJson,
  );
}

/** Grant sources without importing: processing stays frozen. */
async function grantSources(
  candidates: readonly SourceConnectionCandidate[],
  confirmOverrides: boolean,
  operations: CoreOperations,
  asJson: boolean,
): Promise<number> {
  const granted: string[] = [];
  const failed: Record<string, string> = {};
  const overrideRequired: string[] = [];
  for (const candidate of candidates) {
    if (candidate.requires_override_confirmation && !confirmOverrides) {
      overrideRequired.push(candidate.source_id);
      continue;
    }
    const result = await operations.post<unknown>(
      `/api/v1/sources/${encodeURIComponent(candidate.source_id)}/authorize`,
      {
        user_confirmed: true,
        expected_root_id: candidate.candidate_root_id,
        confirm_environment_override:
          confirmOverrides && candidate.requires_override_confirmation,
      },
    );
    if (result.state === "committed") granted.push(candidate.source_id);
    else failed[candidate.source_id] = result.code;
  }
  const failedCount = Object.keys(failed).length + overrideRequired.length;
  const state =
    failedCount === 0
      ? "committed"
      : granted.length === 0
        ? "rejected"
        : "partial";
  const document = {
    schema_version: 1,
    product: PRODUCT,
    command: "sources connect",
    state,
    import: false,
    granted_source_ids: granted,
    failed_sources: failed,
    ...(overrideRequired.length > 0
      ? {
          override_required_source_ids: overrideRequired,
          code:
            granted.length === 0 && Object.keys(failed).length === 0
              ? "environment_override_confirmation_required"
              : undefined,
        }
      : {}),
    next_step:
      "处理默认冻结：不导入历史、不扫描新会话。加 --import 重新运行即导入历史并开启持续同步，或用 memhub sync resume --source <id> 只开启持续同步。",
  };
  const lines = [
    `已接入来源：${granted.join("、") || "无"}`,
    "处理：已冻结（未导入历史，不扫描新会话）",
    ...Object.entries(failed).map(
      ([sourceId, code]) => `接入未完成：${sourceId}（${code}）`,
    ),
    ...(overrideRequired.length > 0
      ? [
          `未接入（目录来自环境变量覆盖，需确认）：${overrideRequired.join("、")}；确认后加 --confirm-overrides 重新运行。`,
        ]
      : []),
    `下一步：${PRODUCT} sources connect <id> --import 导入历史并开启持续同步。`,
  ];
  emit(asJson ? `${JSON.stringify(document)}\n` : `${lines.join("\n")}\n`);
  return state === "committed" ? EXIT_OK : EXIT_FAILED;
}

/** `models receipt <operation-id>`: a creation or a probe.
 *
 * Both are UUIDs minted by the CLI, so the id alone cannot say which. The
 * creation receipt is read first; when Core does not know that id as a
 * creation, the probe receipt is read.
 */
async function emitModelReceipt(
  reader: CoreReader,
  operationId: string,
  asJson: boolean,
): Promise<number> {
  const creation = await readModelReceipt(reader, operationId);
  const unknownCreation =
    creation.state === "not_found" ||
    (creation.state === "unknown" &&
      creation.code === "model_operation_not_found");
  if (!unknownCreation) {
    const document = { ...creation, operation: "model_profile_create" };
    emit(asJson ? `${JSON.stringify(document)}\n` : renderFlowText(creation));
    return flowExitCode(creation);
  }
  const probe = await readModelProbeReceipt(reader, operationId);
  if (probe.state === "not_found") {
    emit(
      asJson
        ? `${JSON.stringify({ schema_version: 1, product: PRODUCT, state: "not_found", operation_id: operationId, code: "model_operation_not_found" })}\n`
        : `未找到该操作：${operationId}\n`,
    );
    return EXIT_FAILED;
  }
  const document = { ...probe, operation: "model_profile_probe" };
  emit(asJson ? `${JSON.stringify(document)}\n` : renderModelProbeText(probe));
  return modelProbeExitCode(probe);
}

/** Attach to a verified Runtime, telling a stopped one (exit 3) from one
 * that is up but unusable (exit 1). */
async function connectRuntime(
  environment: NodeJS.ProcessEnv,
  options?: { readerTimeoutMs?: number },
): Promise<
  | {
      ok: true;
      dataDir: string;
      reader: CoreReader;
      operations: NonNullable<
        Awaited<ReturnType<typeof attachClients>>["operations"]
      >;
    }
  | { ok: false; exitCode: number; code: string }
> {
  const config = resolveRuntimeCliConfig(environment, process.argv[1]);
  const runtime = await inspectRuntimeStatus({
    dataDir: config.dataDir,
    probeIdentity,
    requireServiceReceipt: true,
  });
  if (runtime.state !== "running") {
    return {
      ok: false,
      exitCode:
        runtime.state === "stopped" ? EXIT_RUNTIME_STOPPED : EXIT_FAILED,
      code: runtime.code,
    };
  }
  const clients = await attachClients(config.dataDir, options);
  if (!clients.reader || !clients.operations) {
    return {
      ok: false,
      exitCode: EXIT_FAILED,
      code: clients.failure ?? "runtime_attach_failed",
    };
  }
  return {
    ok: true,
    dataDir: config.dataDir,
    reader: clients.reader,
    operations: clients.operations,
  };
}

/** The verified attachment, or null when the Runtime is absent, unverified
 * or its receipts are unreadable. */
async function attachToRuntimeOrNull(
  environment: NodeJS.ProcessEnv,
): Promise<Awaited<ReturnType<typeof attachToRuntime>>> {
  return attachToRuntime(
    resolveRuntimeCliConfig(environment, process.argv[1]).dataDir,
  ).catch(() => null);
}

/** Core refused or failed a request whose arguments were fine: exit 1 with
 * a machine-readable code, on stdout in JSON mode like every result. */
function emitFailure(
  asJson: boolean,
  command: string,
  code: string,
  nextStep?: string,
  state: "failed" | "rejected" = "failed",
): number {
  if (asJson) {
    process.stdout.write(
      `${JSON.stringify({
        schema_version: 1,
        product: PRODUCT,
        command,
        state,
        code,
        exit_code: EXIT_FAILED,
        ...(nextStep === undefined ? {} : { next_step: nextStep }),
      })}\n`,
    );
  } else {
    process.stderr.write(
      `${command} 未完成：${code}\n${nextStep === undefined ? "" : `下一步：${nextStep}\n`}`,
    );
  }
  return EXIT_FAILED;
}

/**
 * The one confirmation convention: an irreversible command without `--yes`
 * says exactly what it would do, changes nothing, and exits 0.
 */
function emitConfirmPreview(
  asJson: boolean,
  options: {
    command: string;
    target: string;
    effect: string;
    retry: string;
    schemaVersion?: 1 | 2;
  },
): number {
  emit(
    asJson
      ? `${JSON.stringify({
          schema_version: options.schemaVersion ?? 1,
          product: PRODUCT,
          command: options.command,
          state: "preview",
          target: options.target,
          effect: options.effect,
          applied: false,
          next_step: options.retry,
        })}\n`
      : `将要执行：${options.effect}\n未执行任何改动。确认后运行：${options.retry}\n`,
  );
  return EXIT_OK;
}

function renderAdapterProbeText(sourceId: string, value: unknown): string {
  const probe =
    value && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {};
  const fields = Object.entries(probe)
    .filter(([, item]) => item === null || typeof item !== "object")
    .map(([key, item]) => `  ${key}: ${String(item)}`);
  return `来源 ${sourceId} 的探测结果：\n${fields.join("\n") || "  （无字段）"}\n`;
}

function renderWriteReceiptText(receipt: MemoryWriteReceipt): string {
  return [
    `request_id: ${receipt.request_id}`,
    `state: ${receipt.state}`,
    ...(receipt.raw_ids && receipt.raw_ids.length > 0
      ? [`raw_ids: ${receipt.raw_ids.join(",")}`]
      : []),
    ...(receipt.memory_ids && receipt.memory_ids.length > 0
      ? [`memory_ids: ${receipt.memory_ids.join(",")}`]
      : []),
    "",
  ].join("\n");
}

function withQuery(pathname: string, parameters: Record<string, string>) {
  const query = new URLSearchParams(parameters).toString();
  return query ? `${pathname}?${query}` : pathname;
}

function namespaceFlags(namespace: MemoryNamespace): string {
  return `${namespace.kind ? ` --kind ${namespace.kind}` : ""}${
    namespace.variant ? ` --variant ${namespace.variant}` : ""
  }`;
}

/** Poll until the Runtime at `dataDir` reports running, or the budget runs out.
 *
 * Used only after `upgrade` restarts the service. A crash-looping new artifact
 * never answers `probeIdentity`, so this returns false and the caller rolls
 * back. Bounded so a hard failure does not hang the command. */
/** Is this status proof the Runtime is actually serving?
 *
 * `runtime_verified` is the only code that means the receipts are published
 * AND the identity probe succeeded, which is why it carries host/port. A
 * launchd pid (`runtime_service_running`) or any other code is not readiness;
 * treating one as readiness made `start` return in 0s while Core was still
 * seconds from answering.
 */
export function runtimeIsReady(
  status: {
    state?: string;
    code?: string;
    host?: string;
    port?: number;
  } | null,
): boolean {
  return (
    status?.code === "runtime_verified" &&
    status.host !== undefined &&
    status.port !== undefined
  );
}

const SDK_MIGRATION_STEPS: Record<string, string> = {
  bundle: "正在备份记忆数据，数据较多时需要几分钟……",
  prepare: "正在整理待处理的写入任务……",
  cutover: "正在切换记忆数据源……",
  cleanup: "正在清理旧数据表……",
};

/**
 * Migrate a home written by an older release before the new Runtime serves it.
 *
 * Runs the new Runtime's own Core with the Runtime stopped. Progress goes to
 * stderr so stdout stays one result document.
 */
async function migrateSdkDataBeforeStart(options: {
  coreCommand: string;
  dataDir: string;
  runtimeBuild: string;
  asJson: boolean;
  caller?: "cli-install" | "cli-upgrade" | "cli-start" | "cli-restart";
  sourceRuntime?: SdkMigrationSource | null;
}): Promise<SdkMigrationResult> {
  return runSdkMigration({
    dataDir: options.dataDir,
    core: { command: options.coreCommand },
    runtimeBuild: options.runtimeBuild,
    ...(options.caller === undefined ? {} : { caller: options.caller }),
    ...(options.sourceRuntime === undefined
      ? {}
      : { sourceRuntime: options.sourceRuntime }),
    onProgress: (step) => {
      process.stderr.write(
        options.asJson
          ? `${JSON.stringify({ schema_version: 1, product: PRODUCT, event: "sdk_migration.progress", step })}\n`
          : `${SDK_MIGRATION_STEPS[step] ?? step}\n`,
      );
    },
  });
}

class SdkMigrationDeferred extends Error {
  constructor(readonly code: string) {
    super(code);
  }
}

function notReadyAfterMigration(command: "install" | "upgrade") {
  return {
    schema_version: 1,
    product: PRODUCT,
    command,
    status: "error",
    code: "runtime_not_ready_after_sdk_migration",
    message:
      "记忆数据已迁移到新版本，但新版本 Runtime 没有在限定时间内就绪。旧版本无法打开已迁移的数据，所以没有回滚。请运行 memhub restart，或 memhub doctor 查看原因。",
  };
}

/**
 * The new version did not come up and was not rolled back: the previous one
 * may predate the SDK migration gate. `error` carries the underlying code
 * (a failed service restart), never free text from a child process.
 */
function notReadyAfterUpgrade(command: "install" | "upgrade", error?: unknown) {
  return {
    schema_version: 1,
    product: PRODUCT,
    command,
    status: "error",
    code:
      error !== undefined
        ? serviceErrorDocument(command, error).code
        : command === "upgrade"
          ? "upgrade_health_check_failed"
          : "install_runtime_not_ready",
    rolled_back: false,
    message:
      "新版本 Runtime 没有启动或没有在限定时间内就绪。为避免旧版本打开记忆数据，已保留新版本，没有回滚。请运行 memhub restart 重试，或运行 memhub doctor 查看原因；如果仍然失败，请在 GitHub 提交反馈：" +
      MIGRATION_ISSUES_URL,
    issues_url: MIGRATION_ISSUES_URL,
  };
}

const SDK_MIGRATION_FAILURES: Record<string, string> = {
  sdk_migration_disk_space_insufficient:
    "磁盘空间不足，记忆数据迁移没有完成。请腾出与记忆数据同等大小的空间后运行 memhub restart 重试。",
  sdk_migration_memory_unreadable:
    "核对发现新版本还读不到你的全部记忆，没有切换数据源。",
  sdk_migration_model_profile_required:
    "迁移需要先有可用的模型配置，用来核对新版本能否读回记忆。请先用 memhub models setup 完成配置，再运行 memhub restart 重试。",
};

const MIGRATION_ISSUES_URL =
  "https://github.com/Tencent-Hunyuan/Hy-Memory/issues/new";

/**
 * Why MemHub is not serving: the home has not reached SDK authority.
 *
 * Product rule: after an upgrade the home is on SDK authority or MemHub does
 * not serve it at all. Nothing here rolls back to an older Runtime (it has no
 * migration gate and would serve the legacy home); the user gets the code, the
 * migration log and where to report it.
 */
function sdkMigrationFailure(
  command: string,
  code: string,
  dataDir: string,
  context: "upgrade" | "blocked" = "upgrade",
  dataRestored = false,
) {
  const logFile = sdkMigrationLogPath(dataDir);
  // Core put the home back exactly as it was before the upgrade, so the next
  // (fixed) version migrates from the original data.
  const restored = dataRestored ? "记忆数据已恢复到升级前的状态。" : "";
  const reason =
    SDK_MIGRATION_FAILURES[code] ??
    "记忆数据迁移没有完成。可以运行 memhub restart 重试。";
  const lead =
    context === "upgrade"
      ? `${reason}已保留新版本；在迁移完成前 MemHub 不会启动，也不会用旧版本打开未迁移的数据。`
      : `MemHub 没有启动：记忆数据还没有迁移到新版本（${code}）。${reason}`;
  return {
    schema_version: 1,
    product: PRODUCT,
    command,
    status: "error",
    code,
    message: `${lead}原有记忆数据不会丢失。${restored}迁移日志：${logFile}。请在 GitHub 提交反馈并附上这个日志文件：${MIGRATION_ISSUES_URL}`,
    log_file: logFile,
    issues_url: MIGRATION_ISSUES_URL,
    ...(dataRestored ? { data_restored: true } : {}),
  };
}

/** A stable code from a thrown migration error, never its free text. */
function sdkMigrationErrorCode(error: unknown): string {
  const message = error instanceof Error ? error.message : "";
  return /^[a-z][a-z0-9_]{0,95}$/.test(message)
    ? message
    : "sdk_migration_incomplete";
}

/**
 * The Runtime service refuses to spawn Core while this home's migration is
 * deferred and leaves a block note; report that instead of a generic
 * "not ready within budget".
 */
async function runtimeMigrationBlock(
  dataDir: string,
  command: string,
): Promise<ReturnType<typeof sdkMigrationFailure> | null> {
  const block = await readSdkMigrationBlock(dataDir).catch(() => null);
  if (block === null) return null;
  return sdkMigrationFailure(
    command,
    block.code,
    dataDir,
    "blocked",
    block.data_restored === true,
  );
}

async function waitForRuntimeReady(
  dataDir: string,
  timeoutMs = 45_000,
): Promise<boolean> {
  const started = Date.now();
  const deadline = started + timeoutMs;
  while (Date.now() < deadline) {
    const runtime = await inspectRuntimeStatus({
      dataDir,
      probeIdentity,
      requireServiceReceipt: true,
    }).catch(() => null);
    if (runtimeIsReady(runtime)) return true;
    // The service deferred its migration during this wait and will not spawn
    // Core: stop waiting. An older note may predate a retry still running, so
    // only a note written after the wait began ends it early.
    // A frozen home: its service exits without migrating, so nothing will
    // come up until a foreground migration lifts the freeze.
    const blocked = await readSdkMigrationBlock(dataDir).catch(() => null);
    if (
      blocked !== null &&
      (blocked.frozen !== undefined || Date.parse(blocked.at) >= started)
    )
      return false;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return false;
}

async function probeIdentity(
  port: number,
  apiToken: string,
): Promise<HealthResponse> {
  return (
    await axios.get<HealthResponse>(
      `http://127.0.0.1:${port}/api/v1/runtime/identity`,
      {
        headers: { Authorization: `Bearer ${apiToken}` },
        timeout: 2_000,
        proxy: false,
        maxRedirects: 0,
      },
    )
  ).data;
}

function parseOptions(
  arguments_: readonly string[],
): Map<string, string> | null {
  if (arguments_.length % 2 !== 0) return null;
  const allowed = new Set([
    "platform",
    "home",
    "install-root",
    "uid",
    "xdg-config-home",
    "data-dir",
  ]);
  const values = new Map<string, string>();
  for (let index = 0; index < arguments_.length; index += 2) {
    const key = arguments_[index];
    const value = arguments_[index + 1];
    const name = key?.startsWith("--") ? key.slice(2) : "";
    if (
      !allowed.has(name) ||
      values.has(name) ||
      !value ||
      value.startsWith("--")
    )
      return null;
    values.set(name, value);
  }
  return values;
}

/** The read-only inspector must never make status fail. */
async function inspectBridge(
  dataDir: string,
): Promise<BackgroundHealth | null> {
  try {
    return await inspectBackgroundHealth({ dataDir });
  } catch {
    return null;
  }
}

async function readStatusSnapshot(
  dataDir: string,
  signal?: AbortSignal,
  inspectLaunchd?: RunControls["inspectLaunchd"],
  platform: NodeJS.Platform = process.platform,
  serviceId = "com.tencent.onememory.runtime",
): Promise<WatchRead<StatusDocument>> {
  assertNotAborted(signal);
  let runtime = await inspectRuntimeStatus({
    dataDir,
    probeIdentity,
    requireServiceReceipt: true,
  });
  assertNotAborted(signal);
  // Receipts can vanish while a launchd-managed daemon is still alive (e.g.
  // right after a stop that KeepAlive undid). Never report `stopped` when the
  // service is actually running: consult launchd and report the truth.
  if (runtime.state === "stopped" && platform === "darwin") {
    const uid = process.getuid?.();
    if (uid !== undefined) {
      // A global label is only ours when its plist binds this data dir;
      // otherwise a second install would be reported as this one. Tests inject
      // this probe so they never read the host's real LaunchAgent.
      const service = inspectLaunchd
        ? await inspectLaunchd(dataDir).catch(() => null)
        : await inspectLaunchdService({
            serviceId,
            uid,
            runCaptured: runLaunchctlCaptured,
            dataDir,
          }).catch(() => null);
      if (service?.state === "running") {
        const pid = service.pid;
        runtime = {
          state: "running",
          code: "runtime_service_running",
          instanceId: `service-pid-${pid}`,
        };
      }
    }
  }
  const attached =
    runtime.state === "running"
      ? await attachReader(dataDir)
      : { reader: null, failure: undefined };
  assertNotAborted(signal);
  const report = await composeStatusReport({
    dataDir,
    runtime,
    background: await inspectBridge(dataDir),
    reader:
      attached.reader && signal
        ? readerWithSignal(attached.reader, signal)
        : attached.reader,
    attachFailure: attached.failure,
  });
  assertNotAborted(signal);
  return {
    ...(runtime.state === "running" ? { instanceId: runtime.instanceId } : {}),
    value: report,
  };
}

export function managedRuntimeServiceId(
  config: ReturnType<typeof resolveRuntimeCliConfig>,
  environment: NodeJS.ProcessEnv,
  uid: number,
): string {
  const home = config.hostHome ?? os.homedir();
  const layout = runtimeInstallLayout({
    platform: "darwin",
    home,
    installRoot:
      environment.ONE_MEMORY_INSTALL_ROOT ??
      defaultManagedInstallRoot("darwin", home),
    dataDir: config.dataDir,
    ...hourlyIsolationFromEnvironment(environment),
  });
  return planUserService(layout, { uid }).service_id;
}

async function readDoctorSnapshot(
  dataDir: string,
): Promise<{ report: DoctorDocument; runtimeStopped: boolean }> {
  const runtime = await inspectRuntimeStatus({
    dataDir,
    probeIdentity,
    requireServiceReceipt: true,
  });
  const attached =
    runtime.state === "running"
      ? await attachReader(dataDir)
      : { reader: null, failure: undefined };
  const [status, progress] = await Promise.all([
    composeStatusReport({
      dataDir,
      runtime,
      background: await inspectBridge(dataDir),
      reader: attached.reader,
      attachFailure: attached.failure,
    }),
    attached.reader
      ? readProgress(attached.reader, {
          issues: false,
          limit: 500,
        })
      : Promise.resolve(null),
  ]);
  return {
    report: composeDoctorReport(status, progress),
    runtimeStopped: runtime.state === "stopped",
  };
}

async function readProgressSnapshot(
  dataDir: string,
  options: Extract<ReadCommand, { kind: "progress" }>,
  signal: AbortSignal,
): Promise<WatchRead<ProgressDocument>> {
  assertNotAborted(signal);
  const runtime = await inspectRuntimeStatus({
    dataDir,
    probeIdentity,
    requireServiceReceipt: true,
  });
  assertNotAborted(signal);
  if (runtime.state !== "running") {
    return { value: unreadProgress("not_checked", runtime.code) };
  }
  const attached = await attachReader(dataDir);
  assertNotAborted(signal);
  if (!attached.reader) {
    return {
      instanceId: runtime.instanceId,
      value: unreadProgress(
        "unavailable",
        attached.failure ?? "runtime_attach_failed",
      ),
    };
  }
  const value = await readProgress(
    readerWithSignal(attached.reader, signal),
    options,
  );
  assertNotAborted(signal);
  return { instanceId: runtime.instanceId, value };
}

async function runCliWatch<T>(options: {
  kind: "status" | "progress";
  intervalMs: number;
  asJson: boolean;
  controls: RunControls;
  read(signal: AbortSignal): Promise<WatchRead<T>>;
  render(value: T): string;
}): Promise<number> {
  const ownedController = options.controls.watchSignal
    ? null
    : new AbortController();
  const signal = options.controls.watchSignal ?? ownedController?.signal;
  if (!signal) return EXIT_FAILED;
  const cancel = () => ownedController?.abort();
  if (ownedController) {
    process.once("SIGINT", cancel);
    process.once("SIGTERM", cancel);
  }
  try {
    const result = await runWatchLoop({
      kind: options.kind,
      intervalMs: options.intervalMs,
      signal,
      read: options.read,
      maxEvents: options.controls.watchMaxEvents,
      wait: options.controls.watchWait,
      now: options.controls.watchNow,
      emit: (event) =>
        emitWatchEvent(
          event,
          options.asJson,
          options.intervalMs,
          options.render,
        ),
    });
    return result.state === "failed" ? EXIT_FAILED : EXIT_OK;
  } finally {
    if (ownedController) {
      process.off("SIGINT", cancel);
      process.off("SIGTERM", cancel);
    }
  }
}

function emitWatchEvent<T>(
  event: WatchEvent<T>,
  asJson: boolean,
  intervalMs: number,
  render: (value: T) => string,
): void {
  if (asJson) {
    process.stdout.write(`${JSON.stringify(event)}\n`);
    return;
  }
  if (process.stdout.isTTY) {
    process.stdout.write("\x1b[2J\x1b[H");
    process.stdout.write(
      `每 ${Math.round(intervalMs / 1000)} 秒刷新一次，按 Ctrl-C 退出观察，后台不会停止\n`,
    );
  }
  const clock = new Date(event.observed_at);
  const observedAt = process.stdout.isTTY
    ? Number.isNaN(clock.getTime())
      ? event.observed_at
      : clock.toLocaleTimeString("en-GB", { hour12: false })
    : event.observed_at;
  process.stdout.write(`\n[${observedAt}] ${event.event} #${event.sequence}\n`);
  if (event.data !== undefined) {
    process.stdout.write(render(event.data));
  } else {
    process.stdout.write(
      `本轮读取未完成：${event.state ?? "unavailable"}（${event.code ?? "watch_failed"}）\n`,
    );
  }
}

function readerWithSignal(reader: CoreReader, signal: AbortSignal): CoreReader {
  return (path, params) => reader(path, params, { signal });
}

function assertNotAborted(signal?: AbortSignal): void {
  if (signal?.aborted) throw new DOMException("aborted", "AbortError");
}

/** A2 interactive onboarding: pick sources → read/write switches → import?
 *
 * The wizard runs only on a TTY. It reuses the exact same connect path
 * (`connectSourcesWithImport`) and the same access endpoint as
 * `sources connect`, so the UI/CLI/config state cannot diverge. A source that is picked
 * but has read turned off is still granted (so it shows up in `sources list`),
 * matching how the switches are meant to work.
 */
async function runInteractiveInit(
  candidates: readonly SourceConnectionCandidate[],
  clients: {
    reader: CoreReader;
    operations: CoreOperations & CoreDeleteOperations;
  },
  asJson: boolean,
  emitLine: (line: string) => number,
  injected: WizardPrompter | undefined,
  dataDir: string,
  hostHome: string,
): Promise<number> {
  const prompter: WizardPrompter = injected ?? (await createTerminalPrompter());
  try {
    const outcome = await runInitWizard(
      candidates.map((candidate) => ({
        source_id: candidate.source_id,
        display_name: candidate.requires_override_confirmation
          ? `${candidate.display_name}（目录来自环境变量覆盖）`
          : candidate.display_name,
      })),
      prompter,
    );
    if (outcome.state === "cancelled") {
      return emitLine("已取消，未接入任何来源。\n");
    }
    if (outcome.state === "invalid") {
      return reject(undefined, asJson, `交互失败：${outcome.code}`);
    }
    const selected = candidates.filter((candidate) =>
      outcome.answers.sourceIds.includes(candidate.source_id),
    );
    // Freeze grants the source identity but creates no history job. Thaw uses
    // the normal connect path, which creates history work and enables future
    // scanning in the same operation.
    if (!outcome.answers.processing) {
      const sourceCode = await runInteractiveInitGrantOnly(
        selected,
        clients,
        emitLine,
      );
      if (sourceCode !== EXIT_OK || !prompter.askSecret) return sourceCode;
      const setup = await runInteractiveModelSetup(prompter, clients, {
        dataDir,
        hostHome,
        installHosts: true,
      });
      return await finishInteractiveOnboarding(
        setup,
        clients.operations,
        emitLine,
      );
    }
    // Picking a source in the wizard is the user's confirmation of the
    // location shown for it, environment overrides included (the wizard
    // labels those).
    const document = await connectSourcesWithImport(
      clients.reader,
      clients.operations,
      { sourceIds: outcome.answers.sourceIds, confirmOverrides: true },
    );
    if (document.state === "committed" || document.state === "recovered") {
      process.stdout.write(
        `已接入来源：${outcome.answers.sourceIds.join("、")}\n处理：已开启（历史待办与新会话会继续处理）\n操作状态：${document.state}\n`,
      );
      const sourceCode = flowExitCode(document);
      if (sourceCode !== EXIT_OK || !prompter.askSecret) return sourceCode;
      const setup = await runInteractiveModelSetup(prompter, clients, {
        dataDir,
        hostHome,
        installHosts: true,
      });
      return await finishInteractiveOnboarding(
        setup,
        clients.operations,
        emitLine,
      );
    }
    emitLine(
      asJson ? `${JSON.stringify(document)}\n` : renderFlowText(document),
    );
    return flowExitCode(document);
  } finally {
    (prompter as { close?: () => void }).close?.();
  }
}

async function finishInteractiveOnboarding(
  setup: Awaited<ReturnType<typeof runInteractiveModelSetup>>,
  operations: CoreOperations,
  emitLine: (line: string) => number,
): Promise<number> {
  if (setup.state === "failed") return EXIT_FAILED;
  if (setup.state !== "configured") return EXIT_OK;
  const completed = await operations.post<Record<string, unknown>>(
    "/api/v1/onboarding/complete",
    {},
  );
  if (completed.state !== "committed") {
    emitLine(`首次设置未完成：${completed.code}\n`);
    return EXIT_FAILED;
  }
  emitLine("首次设置完成。\n");
  return EXIT_OK;
}

/** Grant selected Harnesses while keeping their processing pipeline frozen. */
async function runInteractiveInitGrantOnly(
  selected: readonly SourceConnectionCandidate[],
  clients: { reader: CoreReader; operations: CoreOperations },
  emitLine: (line: string) => number,
): Promise<number> {
  const granted: string[] = [];
  const failures: Array<{ source_id: string; code: string }> = [];
  for (const candidate of selected) {
    const result = await clients.operations.post<unknown>(
      `/api/v1/sources/${encodeURIComponent(candidate.source_id)}/authorize`,
      {
        user_confirmed: true,
        expected_root_id: candidate.candidate_root_id,
        confirm_environment_override: candidate.requires_override_confirmation,
      },
    );
    if (result.state === "committed") {
      granted.push(candidate.source_id);
    } else {
      failures.push({ source_id: candidate.source_id, code: result.code });
    }
  }
  const lines = [
    `已接入来源：${granted.join("、") || "无"}`,
    "处理：已冻结（不扫描新会话，pending Job 保留）",
    ...failures.map(
      (failure) => `接入未完成：${failure.source_id}（${failure.code}）`,
    ),
  ];
  emitLine(`${lines.join("\n")}\n`);
  return failures.length === 0 ? EXIT_OK : EXIT_FAILED;
}

async function attachClients(
  dataDir: string,
  options?: { readerTimeoutMs?: number },
): Promise<{
  reader: CoreReader | null;
  operations:
    | (CoreOperations &
        CorePutOperations &
        CoreDeleteOperations &
        CorePatchOperations)
    | null;
  failure?: string;
}> {
  try {
    const attachment = await discoverRuntimeAttachment({
      dataDir,
      processIsAlive,
      probeIdentity,
      requireServiceReceipt: true,
    });
    if (attachment === null) {
      return {
        reader: null,
        operations: null,
        failure: "runtime_attach_unavailable",
      };
    }
    return {
      reader: createCoreReader({
        ...attachment,
        ...(options?.readerTimeoutMs === undefined
          ? {}
          : { timeoutMs: options.readerTimeoutMs }),
      }),
      operations: createCoreOperations(attachment),
    };
  } catch (error) {
    return {
      reader: null,
      operations: null,
      failure:
        error instanceof RuntimeDiscoveryError
          ? error.code
          : "runtime_attach_failed",
    };
  }
}

/** Ask Core for a one-time UI exchange and preserve the failure class:
 * missing contract, missing assets, HTTP failure, or transport failure. */
async function requestUiSession(attachment: {
  host: string;
  port: number;
  apiToken: string;
}): Promise<{ url: string; expiresInSeconds: number } | { code: string }> {
  const outcome = await createUiSessionRequest(attachment)();
  if (outcome.state !== "ok") {
    if (outcome.code === "ui_web_assets_unavailable") {
      return { code: outcome.code };
    }
    if (outcome.code === "ui_session_code_invalid") {
      return { code: outcome.code };
    }
    // A 404 means the Core build predates the contract. Other HTTP or
    // transport failures are distinct: reporting every one as a missing
    // contract hid a broken 500 behind unchanged output.
    return {
      code:
        outcome.code === "http_404"
          ? "ui_session_contract_unavailable"
          : outcome.code.startsWith("http_")
            ? "ui_session_exchange_failed"
            : "ui_session_transport_failed",
    };
  }
  const parsed = parseUiSessionExchange(outcome.data);
  return parsed ?? { code: "ui_session_contract_unavailable" };
}

/** Restore a managed backup by starting a *non-owning* maintenance Core.
 *
 * Core refuses restore from a Runtime-owning process, and the CLI has no way
 * to call Core's Python in-process, so it launches `one-memory serve
 * --maintenance` against the same data dir with a freshly generated bearer,
 * waits for the port to answer, calls the restore endpoint, then stops it. Any
 * failure tears the process down and reports; a partial database is never left
 * because Core itself makes the protective copy and the atomic swap.
 */
async function runMaintenanceRestore(options: {
  coreCommand: string;
  dataDir: string;
  source: string;
}): Promise<
  | { state: "restored"; result: RestoreResult }
  | { state: "rejected"; code: string }
> {
  const port = await findFreeLoopbackPort();
  const apiToken = randomUUID().replaceAll("-", "");
  const instanceId = randomUUID();
  const child = spawn(options.coreCommand, ["serve", "--maintenance"], {
    env: {
      // Do not pass the caller's Runtime tuning variables into a
      // maintenance process: a restore must start the product's minimal
      // maintenance configuration, not inherit an unrelated experiment from
      // the shell.
      PATH: process.env.PATH,
      HOME: process.env.HOME,
      LANG: process.env.LANG,
      LC_ALL: process.env.LC_ALL,
      TMPDIR: process.env.TMPDIR,
      ONE_MEMORY_DATA_DIR: options.dataDir,
      ONE_MEMORY_HOST: "127.0.0.1",
      ONE_MEMORY_PORT: String(port),
      ONE_MEMORY_API_TOKEN: apiToken,
      ONE_MEMORY_INSTANCE_ID: instanceId,
    },
    stdio: "ignore",
    windowsHide: true,
  });
  let spawnFailed = false;
  child.once("error", () => {
    spawnFailed = true;
  });
  const stop = async () => {
    if (child.exitCode === null && child.signalCode === null) {
      const signaled = child.kill("SIGTERM");
      if (!signaled) return;
      await new Promise<void>((resolve) => {
        let timer: NodeJS.Timeout;
        const onExit = () => {
          clearTimeout(timer);
          resolve();
        };
        timer = setTimeout(() => {
          child.removeListener("exit", onExit);
          resolve();
        }, 5_000);
        child.once("exit", onExit);
      });
    }
  };
  try {
    const ready = await waitForMaintenanceCore(port, child, () => spawnFailed);
    if (!ready) {
      return { state: "rejected", code: "backup_restore_failed" };
    }
    const identityVerified = await waitForMaintenanceIdentity(
      port,
      instanceId,
      child,
      () => spawnFailed,
    );
    if (!identityVerified) {
      return {
        state: "rejected",
        code: "backup_restore_maintenance_identity_invalid",
      };
    }
    return await callRestore(
      { host: "127.0.0.1", port, apiToken },
      options.source,
    );
  } catch {
    return { state: "rejected", code: "backup_restore_maintenance_failed" };
  } finally {
    await stop();
  }
}

async function waitForMaintenanceIdentity(
  port: number,
  instanceId: string,
  child: { exitCode: number | null; signalCode: NodeJS.Signals | null },
  spawnFailed: () => boolean,
): Promise<boolean> {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    if (spawnFailed() || child.exitCode !== null || child.signalCode !== null) {
      return false;
    }
    if (
      await verifyMaintenanceCoreIdentity({
        host: "127.0.0.1",
        port,
        instanceId,
      })
    ) {
      return true;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return false;
}

async function findFreeLoopbackPort(): Promise<number> {
  const { createServer } = await import("node:net");
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port =
        address === null || typeof address === "string" ? null : address.port;
      server.close(() => {
        if (port === null) reject(new Error("no port"));
        else resolve(port);
      });
    });
  });
}

async function waitForMaintenanceCore(
  port: number,
  child: { exitCode: number | null; signalCode: NodeJS.Signals | null },
  spawnFailed: () => boolean,
): Promise<boolean> {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    if (spawnFailed() || child.exitCode !== null || child.signalCode !== null) {
      return false;
    }
    const ok = await new Promise<boolean>((resolve) => {
      const timer = setTimeout(() => {
        resolve(false);
      }, 500);
      const socket = import("node:net");
      socket.then(({ createConnection }) => {
        const connection = createConnection({ host: "127.0.0.1", port });
        connection.once("connect", () => {
          clearTimeout(timer);
          connection.destroy();
          resolve(true);
        });
        connection.once("error", () => {
          clearTimeout(timer);
          resolve(false);
        });
      });
    });
    if (ok) return true;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return false;
}

/** Attach to the running Runtime, or null when it is absent or unverified.
 *
 * Used by the A6 write commands. Same discovery call every other command uses,
 * so the CLI never reaches Core by a second route.
 */
async function attachToRuntime(
  dataDir: string,
): Promise<Awaited<ReturnType<typeof discoverRuntimeAttachment>> | null> {
  return discoverRuntimeAttachment({
    dataDir,
    processIsAlive,
    probeIdentity,
    requireServiceReceipt: true,
  });
}

async function sha256File(target: string): Promise<string> {
  const digest = createHash("sha256");
  for await (const chunk of createReadStream(target)) digest.update(chunk);
  return digest.digest("hex");
}

async function diagnosticSummary(
  config: ReturnType<typeof resolveRuntimeCliConfig>,
) {
  const hosts = await listHosts(config.hostHome ?? os.homedir()).catch(
    () => null,
  );
  const attachment = await attachToRuntime(config.dataDir).catch(() => null);
  if (!attachment) return { entry: "cli", hosts };
  const read = async (pathname: string): Promise<unknown> => {
    const response = await axios.get(
      `http://${attachment.host}:${attachment.port}${pathname}`,
      {
        headers: { Authorization: `Bearer ${attachment.apiToken}` },
        proxy: false,
        timeout: 2_000,
        maxRedirects: 0,
      },
    );
    return response.data;
  };
  const [sources, processing, models] = await Promise.all([
    read("/api/v1/sources").catch(() => null),
    read("/api/v1/memory-processing").catch(() => null),
    read("/api/v1/model-profiles").catch(() => null),
  ]);
  return {
    entry: "cli",
    sources,
    memory_processing: processing,
    hosts,
    models,
  };
}

async function attachReader(
  dataDir: string,
): Promise<{ reader: CoreReader | null; failure?: string }> {
  try {
    const attachment = await discoverRuntimeAttachment({
      dataDir,
      processIsAlive,
      probeIdentity,
      requireServiceReceipt: true,
    });
    return attachment === null
      ? { reader: null, failure: "runtime_attach_unavailable" }
      : { reader: createCoreReader(attachment) };
  } catch (error) {
    return {
      reader: null,
      failure:
        error instanceof RuntimeDiscoveryError
          ? error.code
          : "runtime_attach_failed",
    };
  }
}

async function attachContextQuery(
  dataDir: string,
): Promise<{ query: CoreContextQuery | null; failure?: string }> {
  try {
    const attachment = await discoverRuntimeAttachment({
      dataDir,
      processIsAlive,
      probeIdentity,
      requireServiceReceipt: true,
    });
    return attachment === null
      ? { query: null, failure: "runtime_attach_unavailable" }
      : { query: createCoreContextQuery(attachment) };
  } catch (error) {
    return {
      query: null,
      failure:
        error instanceof RuntimeDiscoveryError
          ? error.code
          : "runtime_attach_failed",
    };
  }
}

const OPERATION_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
/** Core accepts any 1..256-character request id for `write submit`; the CLI
 * also refuses control characters. */
function isWriteRequestId(value: string): boolean {
  return (
    value.length >= 1 &&
    value.length <= 256 &&
    ![...value].some((character) => {
      const code = character.charCodeAt(0);
      return code <= 31 || code === 127;
    })
  );
}

function parseModelReadCommand(
  rest: readonly string[],
): { kind: "list" } | { kind: "show"; profileId: string } | null {
  if (rest.filter((item) => item === "--json").length > 1) return null;
  const tokens = rest.filter((item) => item !== "--json");
  if (tokens.length === 1 && tokens[0] === "list") return { kind: "list" };
  return tokens.length === 2 &&
    tokens[0] === "show" &&
    OPERATION_ID.test(tokens[1])
    ? { kind: "show", profileId: tokens[1] }
    : null;
}

type ModelsCommand =
  | {
      kind: "add";
      configFile: string;
      apiKeyFile?: string;
      activate: boolean;
    }
  | {
      kind: "edit";
      profileId: string;
      configFile: string;
      apiKeyFile?: string;
    }
  | { kind: "activate"; profileId: string }
  | { kind: "test"; profileId: string }
  | { kind: "receipt"; operationId: string }
  | { kind: "remove"; profileId: string; confirmed: boolean };

/** Read `--flag value` pairs and bare switches after the positional
 * arguments; null on anything unknown, repeated, or missing its value. */
function readFlags(
  tokens: readonly string[],
  valueFlags: readonly string[],
  switchFlags: readonly string[],
): { values: Map<string, string>; switches: Set<string> } | null {
  const values = new Map<string, string>();
  const switches = new Set<string>();
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (switchFlags.includes(token)) {
      if (switches.has(token)) return null;
      switches.add(token);
      continue;
    }
    if (!valueFlags.includes(token) || values.has(token)) return null;
    const value = tokens[index + 1];
    if (value === undefined || value.startsWith("--")) return null;
    values.set(token, value);
    index += 1;
  }
  return { values, switches };
}

/** `models add|edit|activate|test|receipt|remove` (`list`/`show`/`setup` are
 * answered earlier). `--confirm` is a hidden alias of `--yes` on `remove`
 * and is accepted and ignored on `edit`/`activate`, which are now direct. */
function parseModelsCommand(rest: readonly string[]): ModelsCommand | null {
  if (rest.filter((item) => item === "--json").length > 1) return null;
  const tokens = rest.filter((item) => item !== "--json");
  const [action, target] = tokens;
  if (action === "add") {
    const flags = readFlags(
      tokens.slice(1),
      ["--config-file", "--api-key-file"],
      ["--activate"],
    );
    const configFile = flags?.values.get("--config-file");
    if (!flags || configFile === undefined) return null;
    const apiKeyFile = flags.values.get("--api-key-file");
    return {
      kind: "add",
      configFile,
      ...(apiKeyFile === undefined ? {} : { apiKeyFile }),
      activate: flags.switches.has("--activate"),
    };
  }
  if (target === undefined || !OPERATION_ID.test(target)) return null;
  if (action === "edit") {
    const flags = readFlags(
      tokens.slice(2),
      ["--config-file", "--api-key-file"],
      ["--confirm", "--yes"],
    );
    const configFile = flags?.values.get("--config-file");
    if (!flags || configFile === undefined) return null;
    const apiKeyFile = flags.values.get("--api-key-file");
    return {
      kind: "edit",
      profileId: target,
      configFile,
      ...(apiKeyFile === undefined ? {} : { apiKeyFile }),
    };
  }
  if (action === "activate") {
    const flags = readFlags(tokens.slice(2), [], ["--confirm", "--yes"]);
    return flags ? { kind: "activate", profileId: target } : null;
  }
  if (action === "test") {
    return tokens.length === 2 ? { kind: "test", profileId: target } : null;
  }
  if (action === "receipt") {
    return tokens.length === 2
      ? { kind: "receipt", operationId: target }
      : null;
  }
  if (action === "remove") {
    const flags = readFlags(tokens.slice(2), [], ["--yes", "--confirm"]);
    return flags
      ? {
          kind: "remove",
          profileId: target,
          confirmed: flags.switches.size > 0,
        }
      : null;
  }
  return null;
}

type SourcesCommand =
  | {
      kind: "ids";
      sourceIds: string[];
      importHistory: boolean;
      confirmOverrides: boolean;
      requestId?: string;
    }
  | { kind: "all"; importHistory: boolean; confirmOverrides: boolean }
  | { kind: "receipt"; requestId: string };

const SOURCE_ID_SHAPE = /^[a-z0-9][a-z0-9-]{0,127}$/;

/** `sources connect <id> [<id> ...] | --all-detected [--import]
 * [--confirm-overrides] [--request-id <uuid>]` and `sources receipt <id>`.
 *
 * Sources may be named positionally or with repeated `--source` (the old
 * `init --source` spelling is rewritten to this command). */
function parseSourcesCommand(rest: readonly string[]): SourcesCommand | null {
  if (rest.filter((item) => item === "--json").length > 1) return null;
  const tokens = rest.filter((item) => item !== "--json");
  if (tokens[0] === "receipt") {
    return tokens.length === 2 && OPERATION_ID.test(tokens[1])
      ? { kind: "receipt", requestId: tokens[1] }
      : null;
  }
  if (tokens[0] !== "connect") return null;
  const sourceIds: string[] = [];
  const switches = new Set<string>();
  let requestId: string | undefined;
  for (let index = 1; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (["--import", "--confirm-overrides", "--all-detected"].includes(token)) {
      if (switches.has(token)) return null;
      switches.add(token);
      continue;
    }
    if (token === "--source" || token === "--request-id") {
      const value = tokens[index + 1];
      if (value === undefined || value.startsWith("--")) return null;
      if (token === "--source") sourceIds.push(value);
      else if (requestId !== undefined) return null;
      else requestId = value;
      index += 1;
      continue;
    }
    if (token.startsWith("--")) return null;
    sourceIds.push(token);
  }
  if (
    sourceIds.length > 32 ||
    new Set(sourceIds).size !== sourceIds.length ||
    sourceIds.some((id) => !SOURCE_ID_SHAPE.test(id))
  ) {
    return null;
  }
  const importHistory = switches.has("--import");
  const confirmOverrides = switches.has("--confirm-overrides");
  if (
    requestId !== undefined &&
    (!OPERATION_ID.test(requestId) || !importHistory)
  ) {
    return null;
  }
  if (switches.has("--all-detected")) {
    // One names exact sources, the other means "whatever is detected".
    if (sourceIds.length > 0 || requestId !== undefined) return null;
    return { kind: "all", importHistory, confirmOverrides };
  }
  if (sourceIds.length === 0) return null;
  return {
    kind: "ids",
    sourceIds,
    importHistory,
    confirmOverrides,
    ...(requestId === undefined ? {} : { requestId }),
  };
}

function parseGraphWidthCommand(
  rest: readonly string[],
): { decision: GraphWidthDecision; confirmed: boolean } | null {
  if (rest.filter((item) => item === "--json").length > 1) return null;
  const flags = readFlags(
    rest.filter((item) => item !== "--json"),
    ["--decision"],
    ["--yes", "--confirm"],
  );
  const decision = flags?.values.get("--decision");
  if (
    !flags ||
    decision === undefined ||
    !(GRAPH_WIDTH_DECISIONS as readonly string[]).includes(decision)
  ) {
    return null;
  }
  return {
    decision: decision as GraphWidthDecision,
    confirmed: flags.switches.size > 0,
  };
}

function emitFlowDocument(document: FlowDocument, asJson: boolean): void {
  process.stdout.write(
    asJson ? `${JSON.stringify(document)}\n` : renderFlowText(document),
  );
}

function emitModelProbeDocument(
  document: ModelProbeFlowDocument,
  asJson: boolean,
): void {
  process.stdout.write(
    asJson ? `${JSON.stringify(document)}\n` : renderModelProbeText(document),
  );
}

function emitHostDocument(document: HostFlowDocument, asJson: boolean): void {
  process.stdout.write(
    asJson ? `${JSON.stringify(document)}\n` : renderHostText(document),
  );
}

type HostCommand =
  | { kind: "host-list" }
  | {
      kind: "host-status";
      hostId: string;
      operation: HostOperation;
      args: unknown[];
    }
  | {
      kind: "host-apply";
      hostId: string;
      operation: HostOperation;
      args: unknown[];
    };

// Host ids are read with the same grammar as the permission layer and the
// grant store (`@one-memory/contracts`): `deepseek-harness:web` is a real id —
// that host is configured per profile — and `host list` prints it, so rejecting
// it here would mean listing a host the CLI then refuses to act on.
const FINGERPRINT = /^[0-9a-f]{64}$/;

/** Host subcommands.
 *
 * `install`/`uninstall` are split in two on purpose: the preview prints a
 * fingerprint and changes nothing, and the change is only accepted when that
 * fingerprint is presented back. That is the desktop's two-phase confirm, kept
 * so a script cannot install into a host by accident.
 */
function parseHostCommand(rest: readonly string[]): HostCommand | null {
  if (rest.filter((item) => item === "--json").length > 1) return null;
  const tokens = rest.filter((item) => item !== "--json");
  if (tokens.length === 1 && tokens[0] === "list") return { kind: "host-list" };

  let hostId: string | undefined;
  let approve: string | undefined;
  let overwriteModified = false;
  const positional: string[] = [];
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (token === "--host") {
      const value = tokens[index + 1];
      if (!value || !isHostId(value)) return null;
      if (hostId !== undefined) return null;
      hostId = value;
      index += 1;
      continue;
    }
    if (token === "--approve") {
      const value = tokens[index + 1];
      if (!value || !FINGERPRINT.test(value)) return null;
      if (approve !== undefined) return null;
      approve = value;
      index += 1;
      continue;
    }
    if (token === "--overwrite-modified") {
      if (overwriteModified) return null;
      overwriteModified = true;
      continue;
    }
    if (token?.startsWith("--")) return null;
    positional.push(token ?? "");
  }
  // Short form: `memhub host install <host>` (objective's named shape). The
  // first positional is the verb, the second may be the host when `--host` was
  // not given. Ambiguity (both) is rejected rather than guessed.
  let verb = positional[0];
  let qualifier = positional[1];
  if (
    hostId === undefined &&
    verb !== undefined &&
    qualifier !== undefined &&
    (verb === "status" || verb === "install" || verb === "uninstall")
  ) {
    if (!isHostId(qualifier)) return null;
    hostId = qualifier;
    // Drop the consumed host positional so the verb/qualifier checks below see
    // the same shape as the flag form.
    positional.splice(1, 1);
    verb = positional[0];
    qualifier = positional[1];
  }
  // `memhub host sync skill <host>` — host trails the sub-verb here.
  if (
    hostId === undefined &&
    verb === "sync" &&
    qualifier === "skill" &&
    positional[2] !== undefined
  ) {
    const trailing = positional[2];
    if (!isHostId(trailing)) return null;
    hostId = trailing;
    positional.splice(2, 1);
    verb = positional[0];
    qualifier = positional[1];
  }
  if (hostId === undefined || positional.length < 1) return null;
  if (verb === "status") {
    return positional.length === 1
      ? { kind: "host-status", hostId, operation: "getPermission", args: [] }
      : null;
  }
  if (verb === "install") {
    if (positional.length === 1 && approve !== undefined) {
      return {
        kind: "host-apply",
        hostId,
        operation: "install",
        args: [approve],
      };
    }
    return positional.length === 1
      ? { kind: "host-status", hostId, operation: "previewInstall", args: [] }
      : null;
  }
  if (verb === "uninstall") {
    if (positional.length === 1 && approve !== undefined) {
      return {
        kind: "host-apply",
        hostId,
        operation: "uninstall",
        args: [approve],
      };
    }
    return positional.length === 1
      ? { kind: "host-status", hostId, operation: "previewUninstall", args: [] }
      : null;
  }
  if (verb === "sync") {
    if (qualifier !== "skill") return null;
    if (approve !== undefined) {
      return {
        kind: "host-apply",
        hostId,
        operation: "applySkillSync",
        args: [approve, overwriteModified],
      };
    }
    return positional.length === 2
      ? { kind: "host-status", hostId, operation: "previewSkillSync", args: [] }
      : null;
  }
  return null;
}

function emitModelReportDocument(
  document: ModelReportDocument,
  asJson: boolean,
): void {
  process.stdout.write(
    asJson ? `${JSON.stringify(document)}\n` : renderModelReportText(document),
  );
}

function emitSourceSyncDocument(
  document: SourceSyncFlowDocument,
  asJson: boolean,
): void {
  process.stdout.write(
    asJson ? `${JSON.stringify(document)}\n` : renderSourceSyncText(document),
  );
}

type StatusCommand = { watch: boolean; intervalMs: number };

function parseStatusCommand(
  command: "status" | "doctor",
  rest: readonly string[],
): StatusCommand | null {
  if (rest.filter((item) => item === "--json").length > 1) return null;
  const tokens = rest.filter((item) => item !== "--json");
  let watch = false;
  let intervalSeconds = 5;
  let intervalSet = false;
  for (let index = 0; index < tokens.length; index += 1) {
    const flag = tokens[index];
    if (flag === "--watch" && !watch) {
      watch = true;
      continue;
    }
    if (flag !== "--interval" || intervalSet) return null;
    const value = tokens[index + 1];
    if (value === undefined || value.startsWith("--")) return null;
    intervalSeconds = Number(value);
    intervalSet = true;
    index += 1;
  }
  if (command === "doctor" && (watch || intervalSet)) return null;
  if (intervalSet && !watch) return null;
  if (
    !Number.isSafeInteger(intervalSeconds) ||
    intervalSeconds < 1 ||
    intervalSeconds > 300
  ) {
    return null;
  }
  return { watch, intervalMs: intervalSeconds * 1_000 };
}

type ReadCommand =
  | { kind: "sources" }
  | { kind: "sync" }
  | {
      kind: "sync-set";
      action: "pause" | "resume";
      sourceId: string;
    }
  | {
      kind: "progress";
      jobId?: string;
      issues: boolean;
      limit: number;
      watch: boolean;
      intervalMs: number;
    };

const READ_ID = /^[A-Za-z0-9][\w.-]{0,127}$/;
const CONTEXT_SOURCE_ID = /^[a-z0-9][a-z0-9-]{0,127}$/;

type ContextCommand = {
  query: string | null;
  queryFile?: string;
  stdin: boolean;
  kinds: Array<"raw" | "memory">;
  sources: string[];
  limit: number;
  characterBudget: number;
  timeoutMs: number;
};

function parseContextCommand(rest: readonly string[]): ContextCommand | null {
  if (rest.filter((item) => item === "--json").length > 1) return null;
  const tokens = rest.filter((item) => item !== "--json");
  let query: string | null = null;
  let queryFile: string | undefined;
  let stdin = false;
  const kinds: Array<"raw" | "memory"> = [];
  const sources: string[] = [];
  let limit = 8;
  let characterBudget = 4_000;
  let timeoutMs = 2_000;
  let limitSet = false;
  let characterBudgetSet = false;
  let timeoutSet = false;
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (!token.startsWith("--")) {
      if (query !== null || queryFile || stdin) return null;
      query = token;
      continue;
    }
    if (token === "--stdin") {
      if (stdin || query !== null || queryFile) return null;
      stdin = true;
      continue;
    }
    const value = tokens[index + 1];
    if (value === undefined || value.startsWith("--")) return null;
    if (token === "--query-file") {
      if (query !== null || queryFile || stdin) return null;
      queryFile = value;
    } else if (token === "--kind") {
      if ((value !== "raw" && value !== "memory") || kinds.includes(value)) {
        return null;
      }
      kinds.push(value);
    } else if (token === "--source") {
      if (!CONTEXT_SOURCE_ID.test(value) || sources.includes(value))
        return null;
      sources.push(value);
    } else if (token === "--limit" && !limitSet) {
      limit = Number(value);
      limitSet = true;
    } else if (token === "--character-budget" && !characterBudgetSet) {
      characterBudget = Number(value);
      characterBudgetSet = true;
    } else if (token === "--timeout-ms" && !timeoutSet) {
      timeoutMs = Number(value);
      timeoutSet = true;
    } else {
      return null;
    }
    index += 1;
  }
  const selectedKinds: Array<"raw" | "memory"> =
    kinds.length > 0 ? kinds : ["raw", "memory"];
  if (
    (query === null && !queryFile && !stdin) ||
    (sources.length > 0 && !selectedKinds.includes("raw")) ||
    !Number.isSafeInteger(limit) ||
    limit < 1 ||
    limit > 20 ||
    !Number.isSafeInteger(characterBudget) ||
    characterBudget < 200 ||
    characterBudget > 20_000 ||
    !Number.isSafeInteger(timeoutMs) ||
    timeoutMs < 50 ||
    timeoutMs > 30_000
  ) {
    return null;
  }
  if (
    query !== null &&
    (query.length < 1 || query.length > CONTEXT_QUERY_MAX_CHARACTERS)
  ) {
    return null;
  }
  return {
    query,
    ...(queryFile ? { queryFile } : {}),
    stdin,
    kinds: selectedKinds,
    sources,
    limit,
    characterBudget,
    timeoutMs,
  };
}

function parseReadCommand(
  command: "sources" | "sync" | "progress",
  rest: readonly string[],
): ReadCommand | null {
  if (rest.filter((item) => item === "--json").length > 1) return null;
  const tokens = rest.filter((item) => item !== "--json");
  if (command === "sources") {
    return tokens.length === 1 && tokens[0] === "list"
      ? { kind: "sources" }
      : null;
  }
  if (command === "sync") {
    if (tokens.length === 1 && tokens[0] === "status") return { kind: "sync" };
    if (
      (tokens[0] === "pause" || tokens[0] === "resume") &&
      tokens.length === 3 &&
      tokens[1] === "--source" &&
      READ_ID.test(tokens[2])
    ) {
      return { kind: "sync-set", action: tokens[0], sourceId: tokens[2] };
    }
    return null;
  }
  let jobId: string | undefined;
  let issues = false;
  let limit = 50;
  let limitSet = false;
  let watch = false;
  let intervalSeconds = 5;
  let intervalSet = false;
  for (let index = 0; index < tokens.length; index += 1) {
    const flag = tokens[index];
    if (flag === "--issues") {
      if (issues) return null;
      issues = true;
      continue;
    }
    if (flag === "--watch") {
      if (watch) return null;
      watch = true;
      continue;
    }
    const value = tokens[index + 1];
    if (value === undefined || value.startsWith("--")) return null;
    if (flag === "--job" && jobId === undefined) jobId = value;
    else if (flag === "--limit" && !limitSet) {
      limit = Number(value);
      limitSet = true;
    } else if (flag === "--interval" && !intervalSet) {
      intervalSeconds = Number(value);
      intervalSet = true;
    } else return null;
    index += 1;
  }
  if (jobId !== undefined && !READ_ID.test(jobId)) return null;
  if (issues && jobId === undefined) return null;
  if (intervalSet && !watch) return null;
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100) return null;
  if (
    !Number.isSafeInteger(intervalSeconds) ||
    intervalSeconds < 1 ||
    intervalSeconds > 300
  ) {
    return null;
  }
  return {
    kind: "progress",
    jobId,
    issues,
    limit,
    watch,
    intervalMs: intervalSeconds * 1_000,
  };
}

function emitReadDocument(
  command: "sources" | "sync" | "progress",
  document: SourcesDocument | SyncDocument | ProgressDocument,
  asJson: boolean,
): void {
  if (asJson) {
    process.stdout.write(`${JSON.stringify(document)}\n`);
    return;
  }
  process.stdout.write(
    command === "sources"
      ? renderSourcesText(document as SourcesDocument)
      : command === "sync"
        ? renderSyncText(document as SyncDocument)
        : renderProgressText(document as ProgressDocument),
  );
}

type MemoryCommand =
  | { kind: "list"; query: MemoryQuery }
  | { kind: "show"; memoryId: string; namespace: MemoryNamespace }
  | {
      kind: "add";
      text?: string;
      contentFile?: string;
      requestId?: string;
    }
  | { kind: "add-status"; requestId: string }
  | {
      kind: "update";
      memoryId: string;
      contentFile: string;
      namespace: MemoryNamespace;
      task?: string;
    }
  | {
      kind: "delete";
      memoryId: string;
      namespace: MemoryNamespace;
      confirmed: boolean;
    };

/** `--kind`/`--variant`: a chat memory takes no variant, an experience or a
 * fact needs one (working|coding), and a variant never comes alone. */
function readNamespace(values: Map<string, string>): MemoryNamespace | null {
  const kind = values.get("--kind");
  const variant = values.get("--variant");
  if (
    kind !== undefined &&
    !(MEMORY_KINDS as readonly string[]).includes(kind)
  ) {
    return null;
  }
  if (
    variant !== undefined &&
    !(MEMORY_VARIANTS as readonly string[]).includes(variant)
  ) {
    return null;
  }
  if (variant !== undefined && (kind === undefined || kind === "chat")) {
    return null;
  }
  return {
    ...(kind === undefined ? {} : { kind: kind as MemoryKind }),
    ...(variant === undefined ? {} : { variant: variant as MemoryVariant }),
  };
}

async function parseMemoryCommand(
  rest: readonly string[],
): Promise<MemoryCommand | null> {
  if (rest.filter((item) => item === "--json").length > 1) return null;
  const tokens = rest.filter((item) => item !== "--json");
  const action = tokens[0];
  if (action === "add") {
    // `memories add <text>` or `memories add --content-file <path>`.
    const positional =
      tokens[1] !== undefined && !tokens[1].startsWith("--")
        ? tokens[1]
        : undefined;
    const flags = readFlags(
      tokens.slice(positional === undefined ? 1 : 2),
      ["--content-file", "--request-id"],
      [],
    );
    if (!flags) return null;
    const contentFile = flags.values.get("--content-file");
    const requestId = flags.values.get("--request-id");
    if ((positional === undefined) === (contentFile === undefined)) return null;
    if (requestId !== undefined && !isRequestId(requestId)) return null;
    return {
      kind: "add",
      ...(positional === undefined ? {} : { text: positional }),
      ...(contentFile === undefined ? {} : { contentFile }),
      ...(requestId === undefined ? {} : { requestId }),
    };
  }
  if (action === "add-status" || action === "status") {
    return tokens.length === 2 && isRequestId(tokens[1])
      ? { kind: "add-status", requestId: tokens[1] }
      : null;
  }
  if (action === "show" || action === "update" || action === "delete") {
    const memoryId = tokens[1];
    if (!memoryId || !isMemoryId(memoryId)) return null;
    const flags = readFlags(
      tokens.slice(2),
      action === "update"
        ? ["--kind", "--variant", "--content-file", "--task"]
        : ["--kind", "--variant"],
      // `--confirm` is a hidden alias of `--yes` on delete; update is direct
      // and accepts a legacy `--confirm`/`--yes` without effect.
      action === "show" ? [] : ["--yes", "--confirm"],
    );
    if (!flags) return null;
    const namespace = readNamespace(flags.values);
    if (namespace === null) return null;
    if (action === "show") return { kind: "show", memoryId, namespace };
    if (action === "delete") {
      return {
        kind: "delete",
        memoryId,
        namespace,
        confirmed: flags.switches.size > 0,
      };
    }
    const contentFile = flags.values.get("--content-file");
    const task = flags.values.get("--task");
    if (contentFile === undefined) return null;
    // Only an experience has a task; Core refuses it on anything else.
    if (
      task !== undefined &&
      (namespace.kind !== "experience" ||
        task.trim().length === 0 ||
        [...task].length > 10_000)
    ) {
      return null;
    }
    return {
      kind: "update",
      memoryId,
      contentFile,
      namespace,
      ...(task === undefined ? {} : { task }),
    };
  }
  if (action !== "list") return null;
  const flags = readFlags(
    tokens.slice(1),
    ["--status", "--offset", "--limit", "--scene", "--source", "--query"],
    [],
  );
  if (!flags) return null;
  const status = flags.values.get("--status");
  const scene = flags.values.get("--scene");
  const source = flags.values.get("--source");
  const query = flags.values.get("--query");
  const limit = Number(flags.values.get("--limit") ?? 100);
  const offset = Number(flags.values.get("--offset") ?? 0);
  if (
    status !== undefined &&
    !(MEMORY_STATUSES as readonly string[]).includes(status)
  ) {
    return null;
  }
  if (
    scene !== undefined &&
    !(MEMORY_SCENES as readonly string[]).includes(scene)
  ) {
    return null;
  }
  if (source !== undefined && !SOURCE_ID_SHAPE.test(source)) return null;
  if (
    query !== undefined &&
    (query.trim().length === 0 ||
      [...query].length > 512 ||
      // Core searches active memories only.
      (status !== undefined && status !== "active"))
  ) {
    return null;
  }
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 500) return null;
  if (!Number.isSafeInteger(offset) || offset < 0) return null;
  return {
    kind: "list",
    query: {
      ...(status === undefined ? {} : { status }),
      ...(scene === undefined ? {} : { scene: scene as MemoryScene }),
      ...(source === undefined ? {} : { source }),
      ...(query === undefined ? {} : { query }),
      offset,
      limit,
    },
  };
}

async function readMemoryContentFile(file: string): Promise<string | null> {
  const bytes = await readBoundedFile(file, 100_000 * 4 + 2);
  return bytes ? parseMemoryContent(bytes) : null;
}

function emitModelWriteDocument(
  document: ModelWriteDocument,
  asJson: boolean,
): void {
  emit(
    asJson ? `${JSON.stringify(document)}\n` : renderModelWriteText(document),
  );
}

/**
 * Help is an exact request, not a token that wins wherever it appears:
 * `<command> [--help|-h] [--json]` and nothing else. Anything extra goes back
 * to the command's own validation, so a help flag can no longer stand in for a
 * missing option value or hide a typo behind exit 0.
 */
function helpRequest(rest: readonly string[]): "none" | "help" | "invalid" {
  const flags = rest.filter((item) => item === "--help" || item === "-h");
  if (flags.length === 0) return "none";
  const others = rest.filter((item) => item !== "--help" && item !== "-h");
  const onlyJson =
    others.length === 0 || (others.length === 1 && others[0] === "--json");
  return flags.length === 1 && onlyJson ? "help" : "invalid";
}

function explain(topics: readonly string[], asJson: boolean): number {
  const named = topics.filter((item) => item !== "--json");
  const help = findCommand("help");
  if (named.length > 1 || topics.length - named.length > 1) {
    return reject(help, asJson, "参数无效。");
  }
  const requested =
    named[0] === "--help" || named[0] === "-h" ? "help" : named[0];
  if (requested === undefined) {
    return emit(asJson ? renderCatalogDocument() : renderGeneralHelp());
  }
  const descriptor = findCommandOrAlias(requested);
  if (!descriptor) return reject(undefined, asJson, "未知命令。");
  return emit(
    asJson ? renderCatalogDocument(descriptor) : renderCommandHelp(descriptor),
  );
}

function emit(document: string): number {
  process.stdout.write(document);
  return EXIT_OK;
}

function emitLocalExperienceUnavailable(
  command: string,
  confirmed: boolean,
  asJson: boolean,
): number {
  const exitCode = confirmed ? EXIT_FAILED : EXIT_PLAN_BLOCKED;
  const document = {
    schema_version: 1,
    product: PRODUCT,
    command,
    status: "blocked",
    code: "local_experience_operation_unavailable",
    reason: "体验版暂不支持",
    apply: false,
    exit_code: exitCode,
  };
  const output = asJson
    ? `${JSON.stringify(document)}\n`
    : `${command}: 体验版暂不支持 [local_experience_operation_unavailable]\n`;
  (confirmed ? process.stderr : process.stdout).write(output);
  return exitCode;
}

function reject(
  command: CommandDescriptor | undefined,
  asJson: boolean,
  message: string,
): number {
  process.stderr.write(
    asJson
      ? renderUsageErrorDocument(command, message)
      : renderUsageError(command, message),
  );
  return EXIT_USAGE;
}

/** The Runtime is not running (or its identity did not verify).
 *
 * This is not an argument error: the arguments were fine, the backend is
 * simply absent. Every other read command answers this with exit 3 and a
 * `runtime_not_running` code; the lifecycle-adjacent commands that attach
 * directly (`path`/`adapter` list+probe, `write`, `task`, `backup`,
 * `applyPath`) used to fold it into `usage_error`/exit 2, which made a script
 * unable to tell "you typed it wrong" from "start the Runtime".
 */
function runtimeUnavailable(
  command: CommandDescriptor | undefined,
  asJson: boolean,
): number {
  if (asJson) {
    // A result like any other: on stdout, so a `--json` caller reads every
    // outcome from one stream (only usage errors go to stderr).
    process.stdout.write(
      `${JSON.stringify({
        schema_version: 1,
        product: PRODUCT,
        command: command?.name,
        state: "unavailable",
        status: "error",
        code: "runtime_not_running",
        exit_code: EXIT_RUNTIME_STOPPED,
        message: "Runtime 未运行或身份校验未通过。",
        next_step: "运行 memhub start 启动 Runtime。",
      })}\n`,
    );
    return EXIT_RUNTIME_STOPPED;
  }
  process.stderr.write(
    `未完成：runtime_not_running\n下一步：运行 memhub start 启动 Runtime。\n`,
  );
  return EXIT_RUNTIME_STOPPED;
}

/** Run `planLocalInstall`, turning a planning failure into the structured error
 * envelope instead of letting the throw escape as bare text.
 *
 * `install`/`upgrade` refuse an unmanaged `current`, a missing source, an
 * overlapping root and similar conditions at the plan stage. Those used to
 * surface as a raw `Error` on stderr even with `--json`, so a script got
 * unparseable text. Returns the plan, or `null` after writing the envelope. */
async function planLocalInstallOrError(
  command: "install" | "upgrade",
  asJson: boolean,
  source: string,
  installRoot: string,
  allowDowngrade: boolean,
): Promise<Awaited<ReturnType<typeof planLocalInstall>> | null> {
  try {
    return await planLocalInstall({
      source,
      installRoot,
      platform: process.platform,
      arch: process.arch,
      allowDowngrade,
      requireProtocolCompatibility: true,
    });
  } catch (error) {
    process.stderr.write(
      `${JSON.stringify(serviceErrorDocument(command, error), null, 2)}\n`,
    );
    return null;
  }
}

/** A failed mutation, reported with the same envelope as a usage error.
 *
 * `runtime_service_command_failed` on its own told the caller nothing about
 * which command failed or what to check. `next_step` names the two usual
 * causes so the message is actionable without reading CLI source.
 */
function serviceErrorDocument(
  command: string,
  error: unknown,
): Record<string, unknown> {
  const message =
    error instanceof Error && error.message
      ? error.message
      : "runtime_service_command_failed";
  const code = message.split(":", 1)[0];
  const nextSteps: Record<string, string> = {
    runtime_install_incomplete:
      "先运行 memhub install --yes 安装受管 Runtime，再执行 service-install。",
    runtime_service_conflict_unmanaged_label:
      "同名 LaunchAgent 不属于 MemHub；请先确认其来源，CLI 不会停止或覆盖它。",
    runtime_service_conflict_unmanaged_file:
      "服务文件不属于当前 MemHub 安装；请保留文件并确认其来源后再处理。",
    runtime_service_managed_upgrade_failed:
      "新服务启动失败，旧 MemHub 服务已恢复；检查新安装日志后重试。",
    runtime_service_replacement_failed:
      "新服务启动失败；记忆数据尚未处于新版本可用的状态，所以没有恢复旧服务。请运行 memhub doctor 检查服务状态。",
    runtime_service_managed_upgrade_rollback_failed:
      "新服务启动失败且旧服务恢复失败；请运行 memhub doctor 检查服务状态。",
    runtime_service_file_modified:
      "受管服务文件在注册后被改动，CLI 拒绝删除；请确认改动来源后重试或手工处理。",
    runtime_service_file_unmanaged:
      "服务文件不属于当前 MemHub 安装，CLI 拒绝删除；请确认其来源。",
    runtime_service_registration_changed:
      "注册信息在操作期间发生变化，未继续执行；请重新运行以读取最新状态。",
    runtime_service_activation_unverified:
      "服务已注册但 Runtime 未在预算内就绪；请运行 memhub logs 查看启动失败原因。",
    release_version_unavailable:
      "当前 CLI 没有可用于定位 Runtime 的 SemVer；请安装正式 npm 包，或使用 --source 指定离线发行目录。",
    release_manifest_url_unavailable:
      "当前发行构建没有内置 Release 下载源；请使用 --release-manifest-url、MEMHUB_RELEASE_MANIFEST_URL 或 MEMHUB_RELEASE_MANIFEST_URL_TEMPLATE 指定可信 HTTPS 地址。",
    release_manifest_invalid:
      "Release manifest 无效；请确认 CLI 与 Release 版本一致，并检查上传的 release-manifest.json。",
    release_protocol_incompatible:
      "当前 CLI 与该 Runtime 协议不兼容；请升级 CLI，或选择与当前 CLI 同版本的 Release。",
    distribution_protocol_missing:
      "Runtime 缺少兼容协议声明，已拒绝安装；请使用当前版本正式 Release 或重新构建离线包。",
    distribution_protocol_incompatible:
      "当前 CLI 与该离线 Runtime 协议不兼容；请升级 CLI 或使用匹配版本的 --source 包。",
    release_artifact_unavailable:
      "当前平台没有对应 Runtime 附件；请检查该版本 Release 的平台/架构资产。",
    release_artifact_hash_mismatch:
      "Runtime 下载校验失败，未进入安装事务；请重新下载或检查 Release 附件是否被替换。",
    release_artifact_size_mismatch:
      "Runtime 下载大小与 manifest 不一致，已拒绝安装；请检查 Release 附件。",
    release_url_untrusted:
      "Release 跳转到了未授权域名；请核对发布源，或显式配置 MEMHUB_RELEASE_ALLOWED_HOSTS。",
    release_download_timeout:
      "Runtime 下载超时且临时内容已清理；请检查网络/代理后重试。",
    release_disk_space_insufficient:
      "磁盘空间不足，未下载或切换 Runtime；请释放空间后重试。",
    ENOENT:
      command === "install" || command === "upgrade"
        ? "离线发行目录不存在或不完整；请检查 --source 路径及其中的 manifest.json，或移除 --source 改用同版本 Release。"
        : "目标文件不存在；请核对路径后重试。",
  };
  const releaseHttpNextStep = code.startsWith("release_http_status_")
    ? "Release 请求失败；请确认同版本 Release 已发布、manifest URL 可访问且当前账号有权限。"
    : undefined;
  return {
    schema_version: 1,
    product: PRODUCT,
    command,
    status: "error",
    code,
    message,
    exit_code: EXIT_FAILED,
    next_step:
      nextSteps[code] ??
      releaseHttpNextStep ??
      "运行 memhub doctor 检查 Runtime 与用户服务状态，再按报告处理。",
  };
}

function parseServiceOptions(
  arguments_: readonly string[],
  allowConfirmation: boolean,
): { values: Map<string, string>; confirmed: boolean } | null {
  let confirmed = false;
  let json = false;
  const filtered: string[] = [];
  for (const item of arguments_) {
    if (item === "--yes") {
      if (!allowConfirmation || confirmed) return null;
      confirmed = true;
    } else if (item === "--json") {
      // These commands always emit JSON. Accepting the flag keeps `--json`
      // uniform across the CLI instead of failing with a usage error.
      if (json) return null;
      json = true;
    } else filtered.push(item);
  }
  const values = parseOptions(filtered);
  return values ? { values, confirmed } : null;
}

type JobsCommand =
  | { kind: "list"; confirmed: false }
  | { kind: "show"; jobId: string; confirmed: false }
  | {
      kind: "start";
      sourceId: string;
      operation: "history" | "sync";
      confirmed: boolean;
    }
  | {
      kind: "action";
      action: "pause" | "resume" | "cancel";
      jobId: string;
      confirmed: boolean;
    };

function parseJobsCommand(arguments_: readonly string[]): JobsCommand | null {
  if (arguments_.filter((item) => item === "--json").length > 1) return null;
  const normalized = arguments_.filter((item) => item !== "--json");
  if (normalized.length === 1 && normalized[0] === "list") {
    return { kind: "list", confirmed: false };
  }
  if (
    normalized.length === 2 &&
    normalized[0] === "show" &&
    normalized[1] !== undefined &&
    !normalized[1].startsWith("--")
  ) {
    return { kind: "show", jobId: normalized[1], confirmed: false };
  }

  let confirmed = false;
  const values = new Map<string, string>();
  for (let index = 1; index < normalized.length; index += 1) {
    const item = normalized[index];
    if (item === "--confirm") {
      if (confirmed) return null;
      confirmed = true;
      continue;
    }
    const value = normalized[index + 1];
    if (
      value === undefined ||
      value.startsWith("--") ||
      !["--source", "--operation", "--job"].includes(item ?? "") ||
      values.has(item as string)
    ) {
      return null;
    }
    values.set(item as string, value);
    index += 1;
  }
  if (normalized[0] === "start") {
    const sourceId = values.get("--source");
    const operation = values.get("--operation") ?? "history";
    if (
      sourceId === undefined ||
      values.size !== (values.has("--operation") ? 2 : 1) ||
      (operation !== "history" && operation !== "sync")
    ) {
      return null;
    }
    return { kind: "start", sourceId, operation, confirmed };
  }
  const action = normalized[0];
  const jobId = values.get("--job");
  if (
    (action !== "pause" && action !== "resume" && action !== "cancel") ||
    jobId === undefined ||
    values.size !== 1
  ) {
    return null;
  }
  return { kind: "action", action, jobId, confirmed };
}

async function readJobs(reader: CoreReader): Promise<JobFlowDocument> {
  const result = await reader<unknown>("/api/v1/import-jobs");
  if (result.state !== "ok") {
    return {
      schema_version: 1,
      product: PRODUCT,
      command: "jobs",
      state: result.state === "not_found" ? "not_found" : "rejected",
      code: result.code,
    };
  }
  const jobs = Array.isArray(result.data)
    ? result.data.map(validateImportJob)
    : [];
  if (jobs.some((job) => job === null)) {
    return {
      schema_version: 1,
      product: PRODUCT,
      command: "jobs",
      state: "invalid",
      code: "job_receipt_invalid",
    };
  }
  return {
    schema_version: 1,
    product: PRODUCT,
    command: "jobs",
    state: "committed",
    operation_id: "list",
    jobs: jobs.filter((job): job is ImportJob => job !== null),
  } as unknown as JobFlowDocument;
}

async function readJob(
  reader: CoreReader,
  jobId: string,
): Promise<JobFlowDocument> {
  const result = await reader<unknown>(
    `/api/v1/import-jobs/${encodeURIComponent(jobId)}`,
  );
  if (result.state !== "ok") {
    return {
      schema_version: 1,
      product: PRODUCT,
      command: "jobs",
      state: result.state === "not_found" ? "not_found" : "rejected",
      operation_id: jobId,
      code: result.code,
    };
  }
  const job = validateImportJob(result.data);
  if (!job) {
    return {
      schema_version: 1,
      product: PRODUCT,
      command: "jobs",
      state: "invalid",
      operation_id: jobId,
      code: "job_receipt_invalid",
    };
  }
  return {
    schema_version: 1,
    product: PRODUCT,
    command: "jobs",
    state: "committed",
    operation_id: jobId,
    job,
  };
}

function emitJobDocument(document: JobFlowDocument, asJson: boolean): void {
  if (asJson) {
    process.stdout.write(`${JSON.stringify(document)}\n`);
    return;
  }
  if (document.state === "preview") {
    process.stdout.write(`计划：${JSON.stringify(document.plan)}\n`);
    return;
  }
  process.stdout.write(`${JSON.stringify(document)}\n`);
}

function emitJobUnavailable(code: string, asJson: boolean): void {
  const document = {
    schema_version: 1,
    product: PRODUCT,
    command: "jobs",
    state: "rejected",
    code,
  } as const;
  process.stdout.write(asJson ? `${JSON.stringify(document)}\n` : `${code}\n`);
}

function parseInstallOptions(arguments_: readonly string[]): {
  source: string;
  sourceExplicit: boolean;
  releaseManifestUrl?: string;
  releaseChannel: "stable" | "prerelease";
  installRoot: string;
  home: string;
  uid?: number;
  xdgConfigHome?: string;
  binDirectory?: string;
  dataDir?: string;
  shellPathDisabled: boolean;
  shellProfile?: string;
  preserveDataDir: boolean;
  allowDowngrade: boolean;
  confirmed: boolean;
} | null;
function parseInstallOptions(
  arguments_: readonly string[],
  environment: NodeJS.ProcessEnv,
  executable: string | undefined,
): {
  source: string;
  sourceExplicit: boolean;
  releaseManifestUrl?: string;
  releaseChannel: "stable" | "prerelease";
  installRoot: string;
  home: string;
  uid?: number;
  xdgConfigHome?: string;
  binDirectory?: string;
  dataDir?: string;
  shellPathDisabled: boolean;
  shellProfile?: string;
  preserveDataDir: boolean;
  allowDowngrade: boolean;
  confirmed: boolean;
} | null;
function parseInstallOptions(
  arguments_: readonly string[],
  environment: NodeJS.ProcessEnv = process.env,
  executable: string | undefined = process.argv[1],
) {
  let source: string | undefined;
  let releaseManifestUrl: string | undefined;
  let releaseChannel: "stable" | "prerelease" | undefined;
  let installRoot: string | undefined;
  let home: string | undefined;
  let uid: number | undefined;
  let xdgConfigHome: string | undefined;
  let binDirectory: string | undefined;
  let dataDir: string | undefined;
  let shellPathDisabled = false;
  let shellProfile: string | undefined;
  let preserveDataDir = false;
  let allowDowngrade = false;
  let confirmed = false;
  let json = false;
  for (let index = 0; index < arguments_.length; index += 1) {
    const item = arguments_[index];
    if (item === "--yes") {
      if (confirmed) return null;
      confirmed = true;
      continue;
    }
    // Isolation escape hatch: a caller that manages its own PATH (or runs under
    // an isolated HOME) must be able to keep memhub out of the login shell
    // profile entirely. Without this, `install --yes` wrote the real
    // `~/.zprofile` even when HOME/install-root/data-dir were all explicit.
    if (item === "--no-shell-path") {
      if (shellPathDisabled) return null;
      shellPathDisabled = true;
      continue;
    }
    // C7's uninstall guard refuses when the data dir lives inside the subtree
    // it deletes; this flag is the explicit, per-invocation escape hatch. It
    // changes only which directory the plan protects -- never deletes data.
    if (item === "--preserve-data-dir") {
      if (preserveDataDir) return null;
      preserveDataDir = true;
      continue;
    }
    // Explicit, per-invocation opt-in for installing a lower build. The plan
    // records it, so apply re-verifies under the same authorization; it is
    // never enabled by an environment variable or persisted config.
    if (item === "--allow-downgrade") {
      if (allowDowngrade) return null;
      allowDowngrade = true;
      continue;
    }
    if (item === "--json") {
      // `install`/`upgrade`/`uninstall` always emit JSON; accept the flag so it
      // is uniform across commands instead of a usage error. A duplicate is
      // still a malformed argv and must fail closed.
      if (json) return null;
      json = true;
      continue;
    }
    const value = arguments_[index + 1];
    if (!value || value.startsWith("--")) return null;
    if (item === "--source" && source === undefined) source = value;
    else if (
      item === "--release-manifest-url" &&
      releaseManifestUrl === undefined
    )
      releaseManifestUrl = value;
    else if (item === "--channel" && releaseChannel === undefined) {
      if (value !== "stable" && value !== "prerelease") return null;
      releaseChannel = value;
    } else if (item === "--install-root" && installRoot === undefined)
      installRoot = value;
    else if (item === "--home" && home === undefined) home = value;
    else if (item === "--xdg-config-home" && xdgConfigHome === undefined)
      xdgConfigHome = value;
    else if (item === "--bin-dir" && binDirectory === undefined)
      binDirectory = value;
    else if (item === "--data-dir" && dataDir === undefined) dataDir = value;
    else if (item === "--shell-profile" && shellProfile === undefined)
      shellProfile = value;
    else if (item === "--uid" && uid === undefined) {
      uid = Number(value);
      if (!Number.isSafeInteger(uid) || uid < 1) return null;
    } else return null;
    index += 1;
  }
  if (source !== undefined && releaseManifestUrl !== undefined) return null;
  const resolvedHome = home ?? environment.HOME ?? os.homedir();
  const resolvedDataDir = serviceDataDirectory(
    dataDir,
    environment,
    process.platform,
  );
  if (resolvedDataDir === null) return null;
  const inferredSource = executable
    ? path.resolve(path.dirname(executable), "..")
    : undefined;
  const resolvedSource = source ?? inferredSource;
  const resolvedInstallRoot =
    installRoot ??
    defaultInstallRoot(
      process.platform,
      resolvedHome,
      environment.LOCALAPPDATA,
      hourlyIsolationFromEnvironment(environment).hourlyPackageId,
    );
  return resolvedSource
    ? {
        source: resolvedSource,
        sourceExplicit: source !== undefined,
        ...(releaseManifestUrl ? { releaseManifestUrl } : {}),
        releaseChannel:
          releaseChannel ??
          (RUNTIME_RELEASE_VERSION.includes("-") ? "prerelease" : "stable"),
        installRoot: resolvedInstallRoot,
        home: resolvedHome,
        uid: uid ?? process.getuid?.(),
        xdgConfigHome,
        binDirectory,
        dataDir: resolvedDataDir,
        shellPathDisabled,
        ...(shellProfile ? { shellProfile } : {}),
        preserveDataDir,
        allowDowngrade,
        confirmed,
      }
    : null;
}

/** True when this install targets something other than the real account home.
 *
 * `memhub install --home <dir>` (or a `HOME` env that differs from the OS
 * account home) is an isolated or scripted install. Writing the login shell
 * profile there would edit host configuration the caller never asked us to
 * change, so the automatic PATH block is skipped; the caller can still opt in
 * with `--shell-profile <path>`.
 */
function isIsolatedHome(home: string): boolean {
  const resolved = path.resolve(home);
  let accountHome: string;
  try {
    accountHome = os.userInfo().homedir;
  } catch {
    accountHome = os.homedir();
  }
  // A real install resolves to the account home; anything else (an explicit
  // --home, or an isolated HOME in CI/packaged tests) is deliberately scoped.
  return accountHome ? resolved !== path.resolve(accountHome) : true;
}

function serviceDataDirectory(
  explicit: string | undefined,
  environment: NodeJS.ProcessEnv,
  platform: NodeJS.Platform,
): string | undefined | null {
  const value = explicit ?? environment.ONE_MEMORY_DATA_DIR;
  const paths = platform === "win32" ? path.win32 : path.posix;
  if (
    value !== undefined &&
    (!paths.isAbsolute(value) || /[\0\r\n]/.test(value))
  ) {
    return null;
  }
  return value;
}

/** Parse `delete-data`: preview by default; deleting needs `--yes` AND
 * `--confirm <path>` repeating the exact directory (`--yes` alone is refused). */
function parseDataDeletionCommand(
  arguments_: readonly string[],
): { dataDir?: string; confirmed: boolean; confirmPath?: string } | null {
  if (arguments_.filter((item) => item === "--yes").length > 1) return null;
  if (arguments_.filter((item) => item === "--json").length > 1) return null;
  let dataDir: string | undefined;
  let confirmPath: string | undefined;
  let confirmed = false;
  for (let index = 0; index < arguments_.length; index += 1) {
    const item = arguments_[index];
    if (item === "--yes") {
      confirmed = true;
      continue;
    }
    if (item === "--json") continue;
    const value = arguments_[index + 1];
    if (!value || value.startsWith("--")) return null;
    if (item === "--data-dir" && dataDir === undefined) dataDir = value;
    else if (item === "--confirm" && confirmPath === undefined)
      confirmPath = value;
    else return null;
    index += 1;
  }
  return { dataDir, confirmed, confirmPath };
}

/** Extra managed links the CLI must create beside `runtime-control`'s own.
 *
 * `runtime-control/cli-link.ts` owns the managed link names. Its current shape
 * (commit `01eedd8`) declares a `compat_link_path`, meaning it creates BOTH
 * `<bin>/memhub` and `<bin>/one-memory` inside `installCliLink`. In that case
 * the CLI manages none of those two: planning against the pre-install state is
 * inherently stale, and creating them too raised `cli_alias_plan_stale`.
 *
 * The objective nonetheless names `memhub` AND `memhub-cli` as the primary
 * short entries (plus `one-memory` as a compatibility alias). The npm tarball
 * ships all three `bin` names; the managed installer must create the same set,
 * or "装完即短指令可用" is only true for whichever names the package happened
 * to install. So the CLI always plans the ones runtime-control does not own.
 */
const OBJECTIVE_CLI_ALIASES = ["memhub", "memhub-cli", "one-memory"] as const;

function cliCompanionLinkNames(cliLink: {
  link_path: string;
  compat_link_path?: string;
}): string[] {
  const owned = new Set<string>();
  const primary = path.posix.basename(cliLink.link_path);
  owned.add(primary);
  if (typeof cliLink.compat_link_path === "string") {
    owned.add(path.posix.basename(cliLink.compat_link_path));
  }
  return OBJECTIVE_CLI_ALIASES.filter((name) => !owned.has(name));
}

/** Plan the CLI's companion links for install/upgrade (possibly empty).
 *
 * `runtime-control` creates its primary link and the `compat_link_path` itself,
 * so the CLI must not duplicate those on install (`cli_alias_plan_stale`).
 */
async function planCliCompanionLinks(
  cliLink: Parameters<typeof planManagedCliLink>[0],
) {
  const names = cliCompanionLinkNames(cliLink);
  return Promise.all(names.map((name) => planManagedCliLink(cliLink, name)));
}

/** Plan the links the CLI must *remove* on uninstall.
 *
 * `applyLocalUninstall` only deletes `plan.cli_link`; it never deletes the
 * `compat_link_path` it created, and it deletes `versions/`+`current` first, so
 * a compat link left behind becomes a dangling entry on PATH. The CLI therefore
 * cleans up every objective alias that is not the primary link, whether or not
 * `runtime-control` created it. Removing a link that is already gone is a
 * no-op (`not-installed`), so this is safe for installs that predate an alias.
 */
async function planCliRemovalLinks(
  cliLink: Parameters<typeof planManagedCliLink>[0],
) {
  const primary = path.posix.basename(cliLink.link_path);
  const names = OBJECTIVE_CLI_ALIASES.filter((name) => name !== primary);
  return Promise.all(names.map((name) => planManagedCliLink(cliLink, name)));
}

export function defaultInstallRoot(
  platform: NodeJS.Platform,
  home: string,
  localAppData?: string,
  hourlyPackageId?: string,
): string {
  if (platform === "darwin") {
    return defaultManagedInstallRoot(
      platform,
      home,
      localAppData,
      hourlyPackageId !== undefined,
      hourlyPackageId,
    );
  }
  if (platform === "linux" || platform === "win32") {
    return defaultManagedInstallRoot(platform, home, localAppData);
  }
  return path.join(home, ".local", "share", "one-memory");
}

/** Commands that parse `--data-dir` themselves instead of the global lift. */
const OWN_DATA_DIR_COMMANDS = new Set([
  "install",
  "uninstall",
  "upgrade",
  "service-plan",
  "service-install",
  "service-uninstall",
  "delete-data",
  "help",
  "--help",
  "-h",
]);

/** Remove one `--data-dir <absolute path>` from argv; null when it is
 * repeated, missing its value, or not an absolute path. */
function liftDataDirOption(
  arguments_: readonly string[],
): { arguments_: string[]; dataDir?: string } | null {
  const index = arguments_.indexOf("--data-dir");
  if (index === -1) return { arguments_: [...arguments_] };
  if (arguments_.indexOf("--data-dir", index + 1) !== -1) return null;
  const value = arguments_[index + 1];
  if (
    value === undefined ||
    value.startsWith("--") ||
    !path.isAbsolute(value) ||
    /[\0\r\n]/.test(value)
  ) {
    return null;
  }
  return {
    arguments_: [...arguments_.slice(0, index), ...arguments_.slice(index + 2)],
    dataDir: value,
  };
}

/**
 * Old spellings that still work but are no longer listed in help:
 *
 * - `task --id <id>`            -> `write status --id <id>`
 * - `path ...`                  -> `scan-roots ...`
 * - `graph width ...`           -> `graph-width ...`
 * - `init --source/--all-detected ...` -> `sources connect ...`
 */
function rewriteLegacyCommand(arguments_: readonly string[]): string[] {
  const [first, ...rest] = arguments_;
  if (first === "task") {
    // `task --help` explains `write`; anything else is `write status`.
    return rest.some((item) => item === "--help" || item === "-h")
      ? ["write", ...rest]
      : ["write", "status", ...rest];
  }
  if (first === "path") return ["scan-roots", ...rest];
  if (first === "graph" && rest[0] === "width") {
    return ["graph-width", ...rest.slice(1)];
  }
  if (
    first === "init" &&
    rest.some((item) => item === "--source" || item === "--all-detected")
  ) {
    return ["sources", "connect", ...rest];
  }
  return [...arguments_];
}

/** Fill in the install-time data dir when the caller did not name one.
 *
 * Precedence: explicit `ONE_MEMORY_DATA_DIR` > managed install receipt >
 * default `~/.one-memory`. A receipt is only a fallback; an explicit flag or
 * environment variable always wins so scripts and tests stay deterministic.
 */
async function environmentWithManagedDataDir(
  environment: NodeJS.ProcessEnv,
): Promise<NodeJS.ProcessEnv> {
  if (environment.ONE_MEMORY_DATA_DIR !== undefined) return environment;
  const dataDir = await resolveInstalledDataDir(environment);
  return dataDir === undefined
    ? environment
    : { ...environment, ONE_MEMORY_DATA_DIR: dataDir };
}

function runtimeEnvironment(
  config: ReturnType<typeof resolveRuntimeCliConfig>,
): NodeJS.ProcessEnv {
  return {
    ONE_MEMORY_RUNTIME_ENTRY: config.runtimeEntry,
    ONE_MEMORY_DATA_DIR: config.dataDir,
    ONE_MEMORY_CORE_COMMAND: config.coreCommand,
    ONE_MEMORY_MCP_ENTRY: config.mcpEntryPath,
    ONE_MEMORY_RECALL_CLI: config.recallCliPath,
    ONE_MEMORY_SYNC_HOOK_ASSETS: config.syncHookAssetsDirectory,
    ONE_MEMORY_NODE_COMMAND: config.nodeCommand,
  };
}
