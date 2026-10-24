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
import { type CommandDescriptor, findCommand } from "./command-catalog.js";
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
  type CoreUploadOperations,
  createCoreOperations,
} from "./core-operations.js";
import {
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
  executeModelProfile,
  executeSourceImport,
  type FlowDocument,
  flowExitCode,
  inspectExecutionPlan,
  parseApiKey,
  planModelProfile,
  planSourceImport,
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
import { interactiveInitAttemptPath } from "./init-attempt.js";
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
  type JobActionPlan,
  type JobFlowDocument,
  type JobStartPlan,
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
  executeMemoryMutation,
  inspectMemoryPlan,
  type MemoryFlowDocument,
  memoryFlowExitCode,
  memoryPlanAcceptsContent,
  parseMemoryContent,
  planExistingMemoryMutation,
  planMemoryAdd,
  readMemoryMutationReceipt,
  renderMemoryFlowText,
} from "./memory-flows.js";
import {
  isMemoryId,
  type MemoryQuery,
  readMemoryDetail,
  readMemoryPage,
  renderMemoryDetailText,
  renderMemoryPageText,
} from "./memory-report.js";
import {
  executeModelProbe,
  inspectModelProbePlan,
  type ModelProbeFlowDocument,
  modelProbeExitCode,
  planModelProbe,
  readModelProbeReceipt,
  renderModelProbeText,
} from "./model-probe-flow.js";
import {
  type ModelReportDocument,
  modelReportExitCode,
  readModelProfile,
  readModelProfiles,
  renderModelReportText,
} from "./model-report.js";
import {
  executeModelProfileActivate,
  executeModelProfileEdit,
  type ModelWriteDocument,
  modelWriteExitCode,
  planModelProfileActivate,
  planModelProfileEdit,
  renderModelWriteText,
} from "./model-write-flows.js";
import { claimPlanAttempt } from "./operation-journal.js";
import {
  asPlanDocument,
  describe,
  type Plan,
  parseProductCommand,
  parseTaskCommand,
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
  executeCallEventsClear,
  executeCallEventsClearAll,
  executeImportFileCommit,
  executeImportFileDiscard,
  executeImportFileUpload,
  planCallEventsClear,
  planCallEventsClearAll,
  planImportFileCommit,
  planImportFileDiscard,
  readImportFileUpload,
  renderUiWriteText,
  type UiWriteOutcome,
  uiWriteExitCode,
  validateUiWritePlan,
} from "./ui-write-flows.js";
import {
  type ExtendedWriteOutcome,
  type ExtendedWritePlan,
  executeExtractionWindowSet,
  executeGraphWidthDecide,
  executeManualExtract,
  executeMemoryNoteAdd,
  executeMemoryNoteProcess,
  extendedWriteExitCode,
  planExtractionWindowSet,
  planGraphWidthDecide,
  planManualExtract,
  planMemoryNoteAdd,
  planMemoryNoteProcess,
  renderExtendedWriteText,
  validateExtendedWritePlan,
} from "./ui-write-flows-extended.js";
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
  environment = await environmentWithManagedDataDir(environment);
  const asJson = arguments_.includes("--json");
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
  // A6: the product commands. Mutating ones are two-phase — without --yes they
  // print a plan and change nothing, matching the install/service convention.
  // `adapter probe` is read-only too, and is answered here for the same reason
  // as `adapter list` — it has no plan to confirm, so the two-phase branch
  // would reject it for having nothing to apply.
  if (named === "adapter" && arguments_.includes("probe")) {
    // Accept only `adapter probe --source <id>` (plus optional `--json`).
    // Matching on `includes("probe")` alone let a stray flag or extra
    // positional through as if it were a valid invocation.
    const probeTokens = arguments_.filter((item) => item !== "--json");
    if (
      probeTokens.length !== 4 ||
      probeTokens[0] !== "adapter" ||
      probeTokens[1] !== "probe" ||
      probeTokens[2] !== "--source" ||
      probeTokens[3] === undefined ||
      probeTokens[3].startsWith("--")
    ) {
      return usage();
    }
    const source = _sourceArgument(arguments_);
    if (source === null) return usage();
    const attachment = await attachToRuntime(
      resolveRuntimeCliConfig(environment, process.argv[1]).dataDir,
    );
    if (attachment === null) {
      return runtimeUnavailable(descriptor, asJson);
    }
    const document = await probeAdapter(attachment, source);
    const failure = _failureOf(document);
    if (failure !== null) {
      return reject(descriptor, asJson, failure);
    }
    process.stdout.write(
      asJson
        ? `${JSON.stringify({ schema_version: 1, probe: document }, null, 2)}\n`
        : `${JSON.stringify(document)}\n`,
    );
    return EXIT_OK;
  }
  // `adapter list` is read-only too: answered here for the same reason as
  // `path list`, rather than falling into the two-phase branch.
  // `includes` rather than an index: the position of `list` depends on
  // whether `--json` was given, and an index silently missed it.
  if (named === "adapter" && arguments_.includes("list")) {
    // Only `list [--json]` is valid here. Matching on `includes("list")` alone
    // silently accepted a typo'd flag (`adapter list --bogus`) instead of
    // rejecting it, which turns a misspelled option into an unnoticed no-op.
    if (
      arguments_.filter((item) => item === "--json").length > 1 ||
      arguments_.filter((item) => item !== "--json").join(" ") !==
        "adapter list"
    ) {
      return usage();
    }
    const attachment = await attachToRuntime(
      resolveRuntimeCliConfig(environment, process.argv[1]).dataDir,
    );
    if (attachment === null) {
      return runtimeUnavailable(descriptor, asJson);
    }
    const document = await readAdapters(attachment);
    if (document === null) {
      return reject(descriptor, asJson, "adapters 端点不可用。");
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
  // `path list` and `adapter list` are read-only: they have no plan to confirm,
  // so they are answered here rather than falling into the two-phase branch
  // (which would reject them for having nothing to apply).
  // Back to the cold-start screens on purpose. Deleting the app cannot do this
  // (the completion flag lives with the memories, by design), and deleting the
  // data to get there would cost the user everything they have stored.
  if (named === "onboarding" && arguments_.includes("reset")) {
    if (
      arguments_.filter((item) => item === "--json").length > 1 ||
      arguments_.filter((item) => item !== "--json").join(" ") !==
        "onboarding reset"
    ) {
      return usage();
    }
    const attachment = await attachToRuntime(
      resolveRuntimeCliConfig(environment, process.argv[1]).dataDir,
    );
    if (attachment === null) {
      return runtimeUnavailable(descriptor, asJson);
    }
    const state = await resetOnboarding(attachment);
    if (state === null) {
      return reject(descriptor, asJson, "引导状态端点不可用。");
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
  if (named === "path" && arguments_.includes("list")) {
    if (
      arguments_.filter((item) => item === "--json").length > 1 ||
      arguments_.filter((item) => item !== "--json").join(" ") !== "path list"
    ) {
      return usage();
    }
    const attachment = await attachToRuntime(
      resolveRuntimeCliConfig(environment, process.argv[1]).dataDir,
    );
    if (attachment === null) {
      return runtimeUnavailable(descriptor, asJson);
    }
    const document = await readScanRoots(attachment);
    if (document === null) {
      return reject(descriptor, asJson, "scan-roots 端点不可用。");
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
  if (
    named === "path" ||
    named === "adapter" ||
    named === "scan" ||
    named === "backup"
  ) {
    const parsed = parseProductCommand(named, arguments_.slice(1));
    if (!parsed) return usage();
    if (!parsed.confirmed) {
      // Plan only: say what would happen, and that nothing happened.
      process.stdout.write(
        asJson
          ? `${JSON.stringify(asPlanDocument(parsed.plan))}\n`
          : `${describe(parsed.plan)}\n未执行：加 --yes 才会改动。\n`,
      );
      return EXIT_PLAN_BLOCKED;
    }
    // `scan` is wired: Runtime exposes `POST /sources/{id}/sync`. The other
    // product commands are not, and refusing is still better than pretending.
    if (named === "scan") {
      return await applyScan(asJson, descriptor, environment, parsed);
    }
    // `plan.action` is the stable discriminator: the plan the user confirmed is
    // the same object whose action says which change they asked for.
    if (named === "path") {
      return await applyPath(asJson, descriptor, environment, parsed);
    }
    if (named === "backup" && parsed.plan.verb === "create") {
      return await applyBackupCreate(asJson, descriptor, environment, parsed);
    }
    if (named === "backup" && parsed.plan.verb === "restore") {
      // Contract: docs/contracts/c0-ui-and-restore-contract.md (C0, H25).
      // A restore replaces the business database, so it needs a *stopped*
      // Runtime plus an explicit confirmation. Core refuses it from a
      // runtime-owning process; the CLI drives a non-owning maintenance Core.
      if (!restoreConfirmationMatches(parsed.plan.target, parsed.confirmPath)) {
        const document = {
          schema_version: 1 as const,
          product: PRODUCT,
          command: "backup",
          verb: "restore" as const,
          state: "rejected" as const,
          code: "backup_restore_confirmation_required",
          next_step: `再次运行，并把 --source 的路径逐字写到 --confirm：memhub backup restore --source ${parsed.plan.target} --yes --confirm ${parsed.plan.target}`,
        };
        emit(
          asJson
            ? `${JSON.stringify(document)}\n`
            : `backup restore 被拒绝：${document.code}\n下一步：${document.next_step}\n`,
        );
        return EXIT_FAILED;
      }
      const config = resolveRuntimeCliConfig(environment, process.argv[1]);
      const status = await inspectRuntimeStatus({
        dataDir: config.dataDir,
        probeIdentity,
        requireServiceReceipt: true,
      });
      if (status.state === "running") {
        const document = {
          schema_version: 1 as const,
          product: PRODUCT,
          command: "backup",
          verb: "restore" as const,
          state: "rejected" as const,
          code: "backup_restore_runtime_running",
          next_step: `先运行 ${PRODUCT} stop 停止 Runtime；恢复会覆盖现有数据，必须在停机时进行。`,
        };
        emit(
          asJson
            ? `${JSON.stringify(document)}\n`
            : `backup restore 被拒绝：${document.code}\n下一步：${document.next_step}\n`,
        );
        return EXIT_FAILED;
      }
      // A missing runtime receipt is not proof that no Runtime owns the data
      // directory. If the service lease still names a live process, fail
      // closed before starting a maintenance Core.
      const lease = await inspectRuntimeServiceLock(config.dataDir);
      if (lease !== "clear") {
        const document = {
          schema_version: 1 as const,
          product: PRODUCT,
          command: "backup",
          verb: "restore" as const,
          state: "rejected" as const,
          code: "backup_restore_runtime_state_unknown",
          next_step:
            "Runtime 身份无法确认（receipt 缺失但仍有服务锁）。请先运行 memhub stop 并确认进程退出后重试。",
        };
        emit(
          asJson
            ? `${JSON.stringify(document)}\n`
            : `backup restore 被拒绝：${document.code}\n下一步：${document.next_step}\n`,
        );
        return EXIT_FAILED;
      }
      const runMaintenance =
        controls.runMaintenanceRestore ?? runMaintenanceRestore;
      const restored = await runMaintenance({
        coreCommand: config.coreCommand,
        dataDir: config.dataDir,
        source: parsed.plan.target,
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
      const document = {
        schema_version: 1 as const,
        product: PRODUCT,
        command: "backup",
        verb: "restore" as const,
        state: "rejected" as const,
        code: restored.code,
        next_step:
          restored.code === "backup_restore_contract_unavailable"
            ? "当前 Core 不提供受管恢复端点；请升级到包含该合同的 Runtime。"
            : "检查来源是否为受管 backups/ 目录中的 One Memory 备份后重试。",
      };
      emit(
        asJson
          ? `${JSON.stringify(document)}\n`
          : `backup restore 被拒绝：${document.code}\n下一步：${document.next_step}\n`,
      );
      return restored.code === "backup_restore_contract_unavailable"
        ? EXIT_PLAN_BLOCKED
        : EXIT_FAILED;
    }
    return reject(
      descriptor,
      asJson,
      `${named} 的实际执行待 Runtime 接口就绪。`,
    );
  }
  if (named === "write") {
    const parsed = parseWriteCommand(arguments_.slice(1));
    if (!parsed) return usage();
    const attachment = await attachToRuntime(
      resolveRuntimeCliConfig(environment, process.argv[1]).dataDir,
    );
    if (attachment === null) {
      return runtimeUnavailable(descriptor, asJson);
    }
    if (parsed.kind === "status") {
      const receipt = await readMemoryWrite(attachment, parsed.id);
      process.stdout.write(
        asJson
          ? `${JSON.stringify({ schema_version: 1, write: receipt })}\n`
          : receipt === null
            ? "未找到该 request id。\n"
            : `request_id: ${receipt.request_id}\nstate: ${receipt.state}\n`,
      );
      return EXIT_OK;
    }
    const document = await readBoundedJson(parsed.messages_file);
    const messages = parseOpenAIMessages(document);
    if (messages === null) {
      return reject(
        descriptor,
        asJson,
        "messages 文件必须是含 user/assistant/tool 文本消息的 OpenAI messages 数组。",
      );
    }
    const receipt = await submitMemoryWrite(attachment, {
      session: parsed.session,
      source: parsed.source,
      request_id: parsed.request_id ?? randomUUID(),
      messages,
      ...(parsed.memory_at === undefined
        ? {}
        : { memory_at: parsed.memory_at }),
      ...(parsed.priority === undefined ? {} : { priority: parsed.priority }),
    });
    process.stdout.write(
      asJson
        ? `${JSON.stringify({ schema_version: 1, write: receipt })}\n`
        : `request_id: ${receipt.request_id}\nstate: ${receipt.state}\n`,
    );
    return EXIT_OK;
  }
  if (named === "jobs") {
    const parsed = parseJobsCommand(arguments_.slice(1));
    if (!parsed) return usage();
    const config = resolveRuntimeCliConfig(environment, process.argv[1]);
    const clients = await attachClients(config.dataDir);
    if (!clients.reader || !clients.operations) {
      emitJobUnavailable(clients.failure ?? "runtime_attach_failed", asJson);
      return EXIT_RUNTIME_STOPPED;
    }
    if (parsed.kind === "list") {
      const document = await readJobs(clients.reader);
      emitJobDocument(document, asJson);
      return jobExitCode(document);
    }
    if (parsed.kind === "show") {
      const document = await readJob(clients.reader, parsed.jobId);
      emitJobDocument(document, asJson);
      return jobExitCode(document);
    }
    if (parsed.kind === "start") {
      const preview = planJobStart(parsed.sourceId, parsed.operation);
      if (!parsed.confirmed || preview.state !== "preview") {
        emitJobDocument(preview, asJson);
        return jobExitCode(preview);
      }
      const document = await executeJobStart(
        preview.plan as JobStartPlan,
        clients.operations,
      );
      emitJobDocument(document, asJson);
      return jobExitCode(document);
    }
    const preview = planJobAction(parsed.jobId, parsed.action);
    if (!parsed.confirmed || preview.state !== "preview") {
      emitJobDocument(preview, asJson);
      return jobExitCode(preview);
    }
    const document = await executeJobAction(
      preview.plan as JobActionPlan,
      clients.operations,
    );
    emitJobDocument(document, asJson);
    return jobExitCode(document);
  }
  if (named === "task") {
    const parsed = parseTaskCommand(arguments_.slice(1));
    if (!parsed) return usage();
    const attachment = await attachToRuntime(
      resolveRuntimeCliConfig(environment, process.argv[1]).dataDir,
    );
    if (attachment === null) {
      return runtimeUnavailable(descriptor, asJson);
    }
    const receipt = await readMemoryWrite(attachment, parsed.id);
    if (receipt === null) {
      // Unknown id: "never accepted", not "accepted and failed". The caller's
      // next action differs, so this must not be reported as a failure.
      process.stdout.write(
        asJson
          ? `${JSON.stringify({ schema_version: 1, write: null })}\n`
          : "未找到该 request id。\n",
      );
      return EXIT_OK;
    }
    process.stdout.write(
      asJson
        ? `${JSON.stringify({ schema_version: 1, write: receipt })}\n`
        : `request_id: ${receipt.request_id}\nstate: ${receipt.state}\n`,
    );
    return EXIT_OK;
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
          process.stdout.write(
            lifecycle("not-ready", "runtime_not_ready_within_budget"),
          );
          await restoreEnableState();
          return EXIT_FAILED;
        }
        if (restarted === "not-ready") {
          process.stdout.write(
            lifecycle("not-ready", "runtime_not_ready_within_budget"),
          );
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
        process.stdout.write(
          lifecycle("not-ready", "runtime_not_ready_within_budget"),
        );
        await restoreEnableState();
        return EXIT_FAILED;
      }
      if (startedManaged === "not-ready") {
        process.stdout.write(
          lifecycle("not-ready", "runtime_not_ready_within_budget"),
        );
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
          })}\n`
        : state === "stopped"
          ? "MemHub Runtime 已停止。\n"
          : state === "still-running"
            ? "Runtime 由用户服务托管，停用被拒绝或被立即拉起；请运行 memhub service-uninstall。\n"
            : "MemHub Runtime 未运行。\n";
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
      const report = await readDoctorSnapshot(config.dataDir);
      emit(asJson ? `${JSON.stringify(report)}\n` : renderDoctorText(report));
      return doctorExitCode(report);
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
        schema_version: 1,
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
          schema_version: 1,
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
  if (
    named === "setup" ||
    named === "models" ||
    (named === "sources" &&
      arguments_[1] !== "list" &&
      arguments_[1] !== "access" &&
      arguments_[1] !== "apply")
  ) {
    const parsed = parseHeadlessCommand(named, arguments_.slice(1));
    if (!parsed) return usage();
    if (parsed.kind === "model-plan") {
      const input = await readBoundedJson(parsed.configFile);
      if (input === null) return usage();
      const document = planModelProfile(input);
      emitFlowDocument(document, asJson);
      return flowExitCode(document);
    }
    if (parsed.kind === "model-edit-plan") {
      // The patch body carries `expected_revision`, which the caller supplies
      // in the config file just like `models configure`; the plan only echoes
      // the target so nothing needs a Runtime to be previewed.
      const input = await readBoundedJson(parsed.configFile);
      if (input === null) return usage();
      const document = planModelProfileEdit({
        profileId: parsed.profileId,
        patch: input,
      });
      emitModelWriteDocument(document, asJson);
      return modelWriteExitCode(document);
    }
    if (parsed.kind === "model-activate-plan") {
      const document = planModelProfileActivate({
        profileId: parsed.profileId,
      });
      emitModelWriteDocument(document, asJson);
      return modelWriteExitCode(document);
    }
    const config = resolveRuntimeCliConfig(environment, process.argv[1]);
    const runtime = await inspectRuntimeStatus({
      dataDir: config.dataDir,
      probeIdentity,
      requireServiceReceipt: true,
    });
    if (runtime.state !== "running") {
      const document: FlowDocument = {
        schema_version: 1,
        product: PRODUCT,
        state: "unknown",
        code: runtime.code,
      };
      emitFlowDocument(document, asJson);
      return runtime.state === "stopped" ? 3 : 1;
    }
    const clients = await attachClients(config.dataDir);
    if (!clients.reader || !clients.operations) {
      const document: FlowDocument = {
        schema_version: 1,
        product: PRODUCT,
        state: "unknown",
        code: clients.failure ?? "runtime_attach_failed",
      };
      emitFlowDocument(document, asJson);
      return EXIT_FAILED;
    }
    if (
      parsed.kind === "model-activate-apply" ||
      parsed.kind === "model-edit-apply"
    ) {
      let document: ModelWriteDocument;
      if (parsed.kind === "model-activate-apply") {
        const plan = planModelProfileActivate({
          profileId: parsed.profileId,
        });
        document =
          plan.state === "preview" &&
          plan.plan.kind === "model_profile_activate"
            ? await executeModelProfileActivate(plan.plan, clients.operations)
            : plan;
      } else {
        const input = await readBoundedJson(parsed.configFile);
        if (input === null) return usage();
        const planned = planModelProfileEdit({
          profileId: parsed.profileId,
          patch: input,
        });
        if (
          planned.state !== "preview" ||
          planned.plan.kind !== "model_profile_edit"
        ) {
          emitModelWriteDocument(planned, asJson);
          return modelWriteExitCode(planned);
        }
        let apiKey: string | undefined;
        if (parsed.apiKeyFile) {
          apiKey = parseApiKey(
            await readBoundedFile(parsed.apiKeyFile, 16 * 1024 + 2, {
              ownerOnly: true,
            }),
          );
          if (apiKey === undefined) return usage();
        }
        document = await executeModelProfileEdit(
          planned.plan,
          clients.operations,
          apiKey,
        );
      }
      emitModelWriteDocument(document, asJson);
      return modelWriteExitCode(document);
    }
    // Destructive, and never on the same path as a plan. Without --confirm it
    // reports what would happen and changes nothing — the two-phase rule every
    // mutating command here follows.
    if (parsed.kind === "model-remove") {
      if (!parsed.confirm) {
        const preview: FlowDocument = {
          schema_version: 1,
          product: PRODUCT,
          state: "preview",
          plan: {
            schema_version: 1,
            kind: "model_profile_remove",
            target: parsed.profileId,
          },
        };
        emitFlowDocument(preview, asJson);
        return EXIT_PLAN_BLOCKED;
      }
      const removed = await removeModelProfile(
        config.dataDir,
        parsed.profileId,
      );
      if (removed === "not_found" || removed === "active") {
        // Two reasons, two next actions: pick another id, or stop using it
        // first. Naming which one keeps the user from retrying blindly.
        process.stderr.write(
          asJson
            ? `${JSON.stringify({
                schema_version: 1,
                product: PRODUCT,
                state: "rejected",
                code: removed,
              })}\n`
            : removed === "active"
              ? `删除失败：该模型配置正在使用中，请先停用后再删除。\n`
              : `删除失败：未找到该模型配置。\n`,
        );
        return EXIT_FAILED;
      }
      if (removed === "unavailable") {
        const document: FlowDocument = {
          schema_version: 1,
          product: PRODUCT,
          state: "unknown",
          code: "runtime_attach_failed",
        };
        emitFlowDocument(document, asJson);
        return EXIT_FAILED;
      }
      process.stdout.write(
        asJson
          ? `${JSON.stringify({
              schema_version: 1,
              product: PRODUCT,
              state: "committed",
              operation_id: "",
              receipt: { removed: parsed.profileId },
            })}\n`
          : `已删除模型配置：${parsed.profileId}\n`,
      );
      return EXIT_OK;
    }
    if (
      parsed.kind === "model-probe-plan" ||
      parsed.kind === "model-probe" ||
      parsed.kind === "model-probe-receipt"
    ) {
      let probeDocument: ModelProbeFlowDocument;
      if (parsed.kind === "model-probe-plan") {
        probeDocument = await planModelProbe(clients.reader, parsed.profileId);
      } else if (parsed.kind === "model-probe-receipt") {
        probeDocument = await readModelProbeReceipt(
          clients.reader,
          parsed.operationId,
        );
      } else {
        const file = await readBoundedJson(parsed.planFile);
        if (file === null) return usage();
        const plan = unwrapPlan(file);
        const identity = inspectModelProbePlan(plan);
        if (!identity) return usage();
        const attempt = await claimPlanAttempt(parsed.planFile, {
          schema_version: 1,
          operation_id: identity.operation_id,
          plan_digest: identity.digest,
        });
        if (attempt === "invalid") return usage();
        probeDocument =
          attempt === "prior"
            ? await readModelProbeReceipt(clients.reader, identity.operation_id)
            : await executeModelProbe(plan, clients.reader, clients.operations);
      }
      emitModelProbeDocument(probeDocument, asJson);
      return modelProbeExitCode(probeDocument);
    }
    let document: FlowDocument;
    if (parsed.kind === "setup-plan") {
      document = await planSourceImport(clients.reader, parsed.sourceIds);
    } else if (parsed.kind === "source-import") {
      const file = await readBoundedJson(parsed.planFile);
      if (file === null) return usage();
      const plan = unwrapPlan(file);
      const identity = inspectExecutionPlan(plan);
      if (identity?.kind !== "source_import") return usage();
      const attempt = await claimPlanAttempt(parsed.planFile, {
        schema_version: 1,
        operation_id: identity.operationId,
        plan_digest: identity.digest,
      });
      if (attempt === "invalid") return usage();
      document =
        attempt === "prior"
          ? await readSourceReceipt(clients.reader, identity.operationId)
          : await executeSourceImport(plan, clients.reader, clients.operations);
    } else if (parsed.kind === "source-receipt") {
      document = await readSourceReceipt(clients.reader, parsed.requestId);
    } else if (parsed.kind === "model-configure") {
      const file = await readBoundedJson(parsed.planFile);
      if (file === null) return usage();
      const plan = unwrapPlan(file);
      const identity = inspectExecutionPlan(plan);
      if (identity?.kind !== "model_profile_create") return usage();
      let apiKey: string | undefined;
      if (parsed.apiKeyFile) {
        apiKey = parseApiKey(
          await readBoundedFile(parsed.apiKeyFile, 16 * 1024 + 2, {
            ownerOnly: true,
          }),
        );
        if (apiKey === undefined) return usage();
      }
      const attempt = await claimPlanAttempt(parsed.planFile, {
        schema_version: 1,
        operation_id: identity.operationId,
        plan_digest: identity.digest,
      });
      if (attempt === "invalid") return usage();
      document =
        attempt === "prior"
          ? await readModelReceipt(clients.reader, identity.operationId)
          : await executeModelProfile(
              plan,
              apiKey,
              clients.reader,
              clients.operations,
            );
    } else {
      document = await readModelReceipt(clients.reader, parsed.operationId);
    }
    emitFlowDocument(document, asJson);
    return flowExitCode(document);
  }
  if (named === "init") {
    const parsed = parseInitCommand(arguments_.slice(1));
    if (!parsed) return usage();
    const config = resolveRuntimeCliConfig(environment, process.argv[1]);
    const runtime = await inspectRuntimeStatus({
      dataDir: config.dataDir,
      probeIdentity,
      requireServiceReceipt: true,
    });
    if (runtime.state !== "running") {
      emit(
        asJson
          ? `${JSON.stringify({
              schema_version: 1,
              product: PRODUCT,
              command: "init",
              state: "unavailable",
              code: runtime.code,
            })}\n`
          : `未接入：${runtime.code}\n下一步：先运行 ${PRODUCT} start 启动 Runtime。\n`,
      );
      return runtime.state === "stopped" ? 3 : 1;
    }
    const clients = await attachClients(config.dataDir);
    if (!clients.reader || !clients.operations) {
      const code = clients.failure ?? "runtime_attach_failed";
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
    // `--all-detected`: connect and import every source the Runtime actually
    // found. No prompting — this is the CVM/CI shape.
    if (parsed.allDetected) {
      const batch = await planBatchImport(clients.reader);
      if (batch.state !== "preview") {
        const document: BatchScanDocument = {
          schema_version: 1,
          product: PRODUCT,
          command: "init",
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
      if (!parsed.importHistory || parsed.dryRun) {
        emit(
          asJson
            ? `${JSON.stringify({
                schema_version: 1,
                product: PRODUCT,
                command: "init",
                state: "planned",
                source_ids: detectedSourceIds(batch.candidates),
                not_found_source_ids: batch.notFound,
                next_step: parsed.dryRun
                  ? "这是 dry-run，未接入也未导入；去掉 --dry-run 并加 --import 才会执行"
                  : "加 --import 立即导入全部已检测来源的历史会话",
              })}\n`
            : `将接入 ${batch.candidates.length} 个来源：${detectedSourceIds(
                batch.candidates,
              ).join("、")}\n未执行：加 --import 才会真正接入并导入。\n`,
        );
        return EXIT_OK;
      }
      const document = await executeBatchImport(
        batch.candidates,
        clients.reader,
        clients.operations,
        batch.notFound,
      );
      const withCommand: BatchScanDocument = { ...document, command: "init" };
      emit(
        asJson
          ? `${JSON.stringify(withCommand)}\n`
          : renderBatchScanText(withCommand),
      );
      return batchScanExitCode(withCommand);
    }
    // No `--source` and no `--all-detected`: on a TTY, walk the three-step
    // wizard; off a TTY, list what is installed and hand the caller the exact
    // next command. The non-TTY path is deliberately unchanged: a CVM/CI/agent
    // caller must never be prompted or blocked.
    if (parsed.sourceIds === null) {
      const outcome = await clients.reader<unknown>("/api/v1/sources");
      if (outcome.state !== "ok") {
        const code = outcome.code;
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
      const candidates: readonly SourceConnectionCandidate[] | null =
        validateSourceConnectionCandidates(outcome.data);
      if (!candidates) return usage();
      // Interactive only when a real terminal is attached and JSON was not
      // asked for (a machine-readable run must never wait on a prompt).
      const interactive =
        !asJson && (controls.stdinIsTTY ?? process.stdin.isTTY) === true;
      if (interactive && candidates.length > 0) {
        return await runInteractiveInit(
          candidates,
          { reader: clients.reader, operations: clients.operations },
          asJson,
          emit,
          controls.wizardPrompter,
          config.dataDir,
          config.hostHome ?? os.homedir(),
        );
      }
      // Already narrowed inside the validator: it keeps only sources that are
      // available and whose path was actually found, so a host that is merely
      // supported-but-not-installed never reaches this list.
      const available = candidates;
      if (asJson) {
        emit(
          `${JSON.stringify({
            schema_version: 1,
            product: PRODUCT,
            command: "init",
            state: "choose",
            candidates: available.map((candidate) => ({
              source_id: candidate.source_id,
              display_name: candidate.display_name,
              requires_override_confirmation:
                candidate.requires_override_confirmation,
            })),
            next_step: `${PRODUCT} init --source <id> [--import] [--confirm-overrides]`,
          })}\n`,
        );
        return EXIT_OK;
      }
      if (available.length === 0) {
        emit("本机没有检测到可接入的来源。\n");
        return EXIT_OK;
      }
      const lines = available.map(
        (candidate) =>
          `  ${candidate.source_id}${
            candidate.requires_override_confirmation
              ? "  （需 --confirm-overrides）"
              : ""
          }  ${candidate.display_name}`,
      );
      emit(
        `可接入的来源：\n${lines.join("\n")}\n\n下一步：${PRODUCT} init --source <id> [--import]\n`,
      );
      return EXIT_OK;
    }
    // Non-interactive: plan then, when asked, connect and import. Connecting
    // is the grant — there is no separate authorize step.
    const plan = await planSourceImport(clients.reader, parsed.sourceIds);
    if (plan.state !== "preview") {
      emit(asJson ? `${JSON.stringify(plan)}\n` : renderFlowText(plan));
      return flowExitCode(plan);
    }
    // `--dry-run` and "no --import" are the same decision stated two ways,
    // and both mean the same thing: nothing is connected, nothing is written.
    // Kept as separate flags because a caller may ask for an import without a
    // dry run, or want the plan of an import it does intend to run.
    if (!parsed.importHistory || parsed.dryRun) {
      emit(
        asJson
          ? `${JSON.stringify({
              schema_version: 1,
              product: PRODUCT,
              command: "init",
              state: "planned",
              plan,
              dry_run: parsed.dryRun,
              next_step: parsed.dryRun
                ? "这是 dry-run，未接入也未导入；去掉 --dry-run 并加 --import 才会执行"
                : "加 --import 立即导入历史会话",
            })}\n`
          : parsed.dryRun
            ? "已生成接入计划（dry-run：未接入、未导入）。\n"
            : "已生成接入计划。加 --import 可立即导入历史会话。\n",
      );
      return EXIT_OK;
    }
    const identity = inspectExecutionPlan(plan.plan);
    if (identity === null || identity.kind !== "source_import") return usage();
    // The durable attempt fence must live somewhere CLI-owned and stable.
    // The literal `"init"` resolved to a file in whatever directory the user
    // happened to be in, so a second run in the same cwd — or any unrelated
    // `init` file/dir — collided with another run's fence and surfaced as a
    // misleading usage error (exit 2) instead of an idempotent receipt read.
    const attemptPath = initPlanAttemptPath(
      config.dataDir,
      identity.operationId,
    );
    const attempt = await claimPlanAttempt(attemptPath, {
      schema_version: 1,
      operation_id: identity.operationId,
      plan_digest: identity.digest,
    });
    if (attempt === "invalid") return usage();
    const document =
      attempt === "prior"
        ? await readSourceReceipt(clients.reader, identity.operationId)
        : await executeSourceImport(
            plan.plan,
            clients.reader,
            clients.operations,
          );
    emit(asJson ? `${JSON.stringify(document)}\n` : renderFlowText(document));
    return flowExitCode(document);
  }
  if (named === "processing") {
    const parsed = parseManualExtractCommand(arguments_.slice(1));
    if (!parsed) return usage();
    const config = resolveRuntimeCliConfig(environment, process.argv[1]);
    const runtime = await inspectRuntimeStatus({
      dataDir: config.dataDir,
      probeIdentity,
      requireServiceReceipt: true,
    });
    const attached =
      runtime.state === "running"
        ? await attachClients(config.dataDir)
        : { reader: null, operations: null };
    if (runtime.state !== "running" || !attached.operations) {
      const document: ExtendedWriteOutcome = {
        schema_version: 1,
        product: PRODUCT,
        state: "unknown",
        code:
          runtime.state !== "running"
            ? runtime.code
            : (attached.failure ?? "runtime_attach_failed"),
      };
      emit(
        asJson
          ? `${JSON.stringify(document)}\n`
          : renderExtendedWriteText(document),
      );
      return runtime.state === "stopped" ? 3 : 1;
    }
    const document = await applyExtendedWritePlan(parsed, attached.operations);
    emit(
      asJson
        ? `${JSON.stringify(document)}\n`
        : renderExtendedWriteText(document),
    );
    return extendedWriteExitCode(document);
  }
  if (named === "sources" || named === "sync" || named === "progress") {
    const parsed = parseReadCommand(named, arguments_.slice(1));
    if (!parsed) return usage();
    if (parsed.kind === "sync-set") {
      const config = resolveRuntimeCliConfig(environment, process.argv[1]);
      const runtime = await inspectRuntimeStatus({
        dataDir: config.dataDir,
        probeIdentity,
        requireServiceReceipt: true,
      });
      if (runtime.state !== "running") {
        const document: SourceSyncFlowDocument = {
          schema_version: 1,
          product: PRODUCT,
          state: "unknown",
          code: runtime.code,
        };
        emitSourceSyncDocument(document, asJson);
        return runtime.state === "stopped" ? 3 : 1;
      }
      const clients = await attachClients(config.dataDir);
      if (!clients.reader || !clients.operations) {
        const document: SourceSyncFlowDocument = {
          schema_version: 1,
          product: PRODUCT,
          state: "unknown",
          code: clients.failure ?? "runtime_attach_failed",
        };
        emitSourceSyncDocument(document, asJson);
        return EXIT_FAILED;
      }
      const document = await setSourceSync(clients.reader, clients.operations, {
        sourceId: parsed.sourceId,
        action: parsed.action,
      });
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
    if (arguments_.slice(1).some((value) => value !== "--json")) return usage();
    const config = resolveRuntimeCliConfig(environment, process.argv[1]);
    const attached = await attachClients(config.dataDir, {
      readerTimeoutMs: 15_000,
    });
    if (!attached.reader) {
      const document = {
        schema_version: 2,
        product: PRODUCT,
        state: "unavailable",
        code: attached.failure ?? "runtime_attach_failed",
      };
      emit(
        asJson
          ? `${JSON.stringify(document)}\n`
          : `用户画像读取失败：${document.code}\n`,
      );
      return EXIT_RUNTIME_STOPPED;
    }
    const outcome = await attached.reader<unknown>("/api/v2/user-profile");
    if (outcome.state !== "ok") {
      const document = {
        schema_version: 2,
        product: PRODUCT,
        state: outcome.state,
        code: outcome.code,
      };
      emit(
        asJson
          ? `${JSON.stringify(document)}\n`
          : `用户画像读取失败：${outcome.code}\n`,
      );
      return EXIT_FAILED;
    }
    const value = outcome.data as {
      request_id?: unknown;
      profile?: unknown;
      elapsed_ms?: unknown;
    };
    if (
      typeof value.request_id !== "string" ||
      !(
        value.profile === null ||
        (typeof value.profile === "object" && !Array.isArray(value.profile))
      ) ||
      typeof value.elapsed_ms !== "number"
    ) {
      return reject(descriptor, asJson, "用户画像响应格式无效。");
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
    const config = resolveRuntimeCliConfig(environment, process.argv[1]);
    const runtime = await inspectRuntimeStatus({
      dataDir: config.dataDir,
      probeIdentity,
      requireServiceReceipt: true,
    });
    let reader: CoreReader | null = null;
    let operations: CoreOperations | null = null;
    let blocked: { state: string; code: string } | null = null;
    if (runtime.state === "running") {
      const attached = await attachClients(config.dataDir, {
        readerTimeoutMs: 15_000,
      });
      reader = attached.reader;
      operations = attached.operations;
      if (!reader) {
        blocked = {
          state: "unavailable",
          code: attached.failure ?? "runtime_attach_failed",
        };
      }
    } else {
      blocked = {
        state: runtime.state === "stopped" ? "not_checked" : "unavailable",
        code: runtime.code,
      };
    }
    if (blocked || !reader) {
      if (
        parsed.kind === "update" ||
        parsed.kind === "delete" ||
        parsed.kind === "plan-add" ||
        parsed.kind === "plan-existing" ||
        parsed.kind === "apply"
      ) {
        const document: MemoryFlowDocument = {
          schema_version: 1,
          product: PRODUCT,
          state: "unknown",
          code: blocked?.code ?? "runtime_attach_failed",
        };
        emitMemoryFlow(document, asJson);
        return runtime.state === "stopped" ? 3 : 1;
      }
      const document =
        parsed.kind === "list"
          ? { schema_version: 1, product: PRODUCT, page: blocked, memories: [] }
          : {
              schema_version: 1,
              product: PRODUCT,
              memory: blocked,
              evidence: [],
              history: [],
            };
      emit(
        asJson
          ? `${JSON.stringify(document)}\n`
          : `记忆读取未完成：${blocked?.state}（${blocked?.code}）\n`,
      );
      return runtime.state === "stopped" ? 3 : 1;
    }
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
      const document = await readMemoryDetail(reader, parsed.memoryId);
      emit(
        asJson
          ? `${JSON.stringify(document)}\n`
          : renderMemoryDetailText(document),
      );
      return document.memory.state === "ok" ? EXIT_OK : EXIT_FAILED;
    }
    if (parsed.kind === "update" || parsed.kind === "delete") {
      const content =
        parsed.kind === "update"
          ? await readMemoryContentFile(parsed.contentFile)
          : null;
      if (parsed.kind === "update" && content === null) return usage();
      const plan = {
        schema_version: 2,
        product: PRODUCT,
        state: "preview",
        operation: parsed.kind,
        memory_id: parsed.memoryId,
        ...(content === null
          ? {}
          : {
              content_sha256: createHash("sha256")
                .update(content)
                .digest("hex"),
            }),
      };
      if (!parsed.confirmed) {
        emit(
          asJson
            ? `${JSON.stringify(plan)}\n`
            : `计划：${parsed.kind} ${parsed.memoryId}\n使用 --confirm 执行。\n`,
        );
        return EXIT_OK;
      }
      if (!operations) return EXIT_FAILED;
      const writable = operations as CoreOperations &
        CorePutOperations &
        CoreDeleteOperations;
      const path = `/api/v2/memories/${encodeURIComponent(parsed.memoryId)}`;
      const outcome =
        parsed.kind === "update"
          ? await writable.put<unknown>(path, { content })
          : await writable.delete<unknown>(path);
      const document = {
        schema_version: 2,
        product: PRODUCT,
        state: outcome.state,
        operation: parsed.kind,
        memory_id: parsed.memoryId,
        ...(outcome.state === "committed"
          ? { result: outcome.data }
          : { code: outcome.code }),
      };
      emit(
        asJson
          ? `${JSON.stringify(document)}\n`
          : outcome.state === "committed"
            ? `已${parsed.kind === "update" ? "更新" : "删除"}记忆 ${parsed.memoryId}\n`
            : `操作失败：${outcome.code}\n`,
      );
      return outcome.state === "committed" ? EXIT_OK : EXIT_FAILED;
    }
    if (parsed.kind === "plan-add") {
      const content = await readMemoryContentFile(parsed.contentFile);
      if (content === null) return usage();
      const document = planMemoryAdd({
        content,
        memoryKind: parsed.memoryKind,
        scopes: parsed.scopes,
        evidenceIds: parsed.evidenceIds,
      });
      emitMemoryFlow(document, asJson);
      return memoryFlowExitCode(document);
    }
    if (parsed.kind === "plan-existing") {
      let content: string | undefined;
      if (parsed.contentFile) {
        const read = await readMemoryContentFile(parsed.contentFile);
        if (read === null) return usage();
        content = read;
      }
      const document = await planExistingMemoryMutation(reader, {
        operation: parsed.operation,
        memoryId: parsed.memoryId,
        ...(content === undefined ? {} : { content }),
      });
      emitMemoryFlow(document, asJson);
      return memoryFlowExitCode(document);
    }
    if (!operations) {
      const document: MemoryFlowDocument = {
        schema_version: 1,
        product: PRODUCT,
        state: "unknown",
        code: "runtime_attach_failed",
      };
      emitMemoryFlow(document, asJson);
      return EXIT_FAILED;
    }
    const file = await readBoundedJson(parsed.planFile);
    if (file === null) return usage();
    const plan = unwrapPlan(file);
    const identity = inspectMemoryPlan(plan);
    if (!identity) return usage();
    let content: string | undefined;
    if (parsed.contentFile) {
      const read = await readMemoryContentFile(parsed.contentFile);
      if (read === null) return usage();
      content = read;
    }
    if (
      (identity.needsContent && content === undefined) ||
      (!identity.needsContent && content !== undefined) ||
      !memoryPlanAcceptsContent(plan, content)
    ) {
      return usage();
    }
    const attempt = await claimPlanAttempt(parsed.planFile, {
      schema_version: 1,
      operation_id: identity.operationId,
      plan_digest: identity.digest,
    });
    if (attempt === "invalid") return usage();
    const document =
      attempt === "prior"
        ? await readMemoryMutationReceipt(reader, plan)
        : await executeMemoryMutation(plan, content, operations, reader);
    emitMemoryFlow(document, asJson);
    return memoryFlowExitCode(document);
  }
  if (named === "calls" || named === "import-file") {
    const parsed = parseUiWriteCommand(named, arguments_.slice(1));
    if (!parsed) return usage();
    const config = resolveRuntimeCliConfig(environment, process.argv[1]);
    const runtime = await inspectRuntimeStatus({
      dataDir: config.dataDir,
      probeIdentity,
      requireServiceReceipt: true,
    });
    let clients: {
      operations:
        | (CoreOperations &
            CorePutOperations &
            CoreUploadOperations &
            CoreDeleteOperations)
        | null;
      failure?: string;
    } = { operations: null };
    if (runtime.state === "running") {
      const attached = await attachClients(config.dataDir);
      clients = {
        operations: attached.operations,
        ...(attached.failure ? { failure: attached.failure } : {}),
      };
    }
    if (runtime.state !== "running" || !clients.operations) {
      const document: UiWriteOutcome = {
        schema_version: 1,
        product: PRODUCT,
        state: "unknown",
        code:
          runtime.state !== "running"
            ? runtime.code
            : (clients.failure ?? "runtime_attach_failed"),
      };
      emit(
        asJson ? `${JSON.stringify(document)}\n` : renderUiWriteText(document),
      );
      return runtime.state === "stopped" ? 3 : 1;
    }
    const document = await applyUiWriteCommand(parsed, clients.operations);
    emit(
      asJson ? `${JSON.stringify(document)}\n` : renderUiWriteText(document),
    );
    return uiWriteExitCode(document);
  }
  if (named === "notes" || named === "extraction" || named === "graph") {
    const parsed = parseExtendedWriteCommand(
      named as "notes" | "extraction" | "graph",
      arguments_.slice(1),
    );
    if (!parsed) return usage();
    const config = resolveRuntimeCliConfig(environment, process.argv[1]);
    const runtime = await inspectRuntimeStatus({
      dataDir: config.dataDir,
      probeIdentity,
      requireServiceReceipt: true,
    });
    let clients: {
      operations:
        | (CoreOperations &
            CorePutOperations &
            CoreUploadOperations &
            CoreDeleteOperations)
        | null;
      failure?: string;
    } = { operations: null };
    if (runtime.state === "running") {
      const attached = await attachClients(config.dataDir);
      clients = {
        operations: attached.operations,
        ...(attached.failure ? { failure: attached.failure } : {}),
      };
    }
    if (runtime.state !== "running" || !clients.operations) {
      const document: ExtendedWriteOutcome = {
        schema_version: 1,
        product: PRODUCT,
        state: "unknown",
        code:
          runtime.state !== "running"
            ? runtime.code
            : (clients.failure ?? "runtime_attach_failed"),
      };
      emit(
        asJson
          ? `${JSON.stringify(document)}\n`
          : renderExtendedWriteText(document),
      );
      return runtime.state === "stopped" ? 3 : 1;
    }
    const operations = clients.operations;
    const applied = await applyExtendedWritePlan(parsed, operations);
    emit(
      asJson
        ? `${JSON.stringify(applied)}\n`
        : renderExtendedWriteText(applied),
    );
    return extendedWriteExitCode(applied);
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
      .catch((error: unknown) => {
        process.stderr.write(
          `${JSON.stringify(
            serviceErrorDocument("service-install", error),
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
      const migration = await (
        controls.migrateSdkData ?? migrateSdkDataBeforeStart
      )({
        coreCommand: layout.coreCommand,
        dataDir: layout.dataDir,
        runtimeBuild: String(plan.build_number ?? plan.version),
        asJson: true,
      });
      if (migration.state === "deferred") {
        throw new SdkMigrationDeferred(
          migration.code ?? "sdk_migration_incomplete",
        );
      }
      migrated = migration.state === "cutover";
      await applyPreparedServiceInstall(preparedService);
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
      if (migrated) {
        // An older Runtime refuses a migrated home: keep this install.
        process.stderr.write(
          `${JSON.stringify(notReadyAfterMigration("install"), null, 2)}\n`,
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
        `${JSON.stringify(
          error instanceof SdkMigrationDeferred
            ? sdkMigrationFailure("install", error.code)
            : serviceErrorDocument("install", error),
          null,
          2,
        )}\n`,
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
      process.stdout.write(
        `${JSON.stringify({ ...plan, apply: false }, null, 2)}\n`,
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
      // `--yes` alone must never delete data: the second confirmation names the
      // exact directory, matching the contract's "independent operation and
      // second confirmation" rule.
      process.stderr.write(
        `${JSON.stringify(
          {
            schema_version: 1,
            product: PRODUCT,
            command: "delete-data",
            status: "error",
            code: "data_deletion_confirmation_required",
            exit_code: EXIT_USAGE,
            next_step: `再次运行并显式确认目标目录：memhub delete-data --data-dir ${dataDir} --yes --confirm ${dataDir}`,
          },
          null,
          2,
        )}\n`,
      );
      return EXIT_USAGE;
    }
    try {
      const receipt = await applyDataDeletion(plan, parsed.confirmPath);
      await removeInstallReceipt(
        defaultInstallReceiptPath(environment.HOME ?? os.homedir()),
      );
      process.stdout.write(
        `${JSON.stringify({ ...receipt, apply: true }, null, 2)}\n`,
      );
      return EXIT_OK;
    } catch (error) {
      process.stderr.write(
        `${JSON.stringify(
          {
            schema_version: 1,
            product: PRODUCT,
            command: "delete-data",
            status: "error",
            code:
              error instanceof Error ? error.message : "data_deletion_failed",
            exit_code: EXIT_FAILED,
            next_step: "未完成删除；请确认目标目录与确认参数一致后重试。",
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
    const rollbackUpgrade = async (
      dataDir: string | undefined,
    ): Promise<void> => {
      for (const receipt of memhubReceipts.reverse()) {
        await rollbackManagedCliLink(receipt);
      }
      await rollbackLocalInstall(receipt);
      if (previousOwner !== undefined) {
        await restoreServiceOwnership(layout.dataDir, previousOwner).catch(
          () => undefined,
        );
      }
      await restartUserService(
        runtimeInstallLayout({
          platform: plan.platform,
          home: parsed.home,
          installRoot: canonicalManagedServiceRoot(
            plan.platform,
            plan.current_link,
          ),
          dataDir,
          ...hourlyIsolationFromEnvironment(environment),
        }),
        serviceOptions,
      ).catch(() => undefined);
    };
    try {
      for (const link of companionLinks) {
        memhubReceipts.push(await installManagedCliLink(link));
      }
      previousOwner = await claimCliServiceOwnership(layout.dataDir);
      const migration = await (
        controls.migrateSdkData ?? migrateSdkDataBeforeStart
      )({
        coreCommand: layout.coreCommand,
        dataDir: parsed.dataDir ?? layout.dataDir,
        runtimeBuild: String(plan.build_number ?? plan.version),
        asJson,
      });
      if (migration.state === "deferred") {
        await rollbackUpgrade(parsed.dataDir);
        process.stderr.write(
          `${JSON.stringify(sdkMigrationFailure("upgrade", migration.code ?? "sdk_migration_incomplete"), null, 2)}\n`,
        );
        return EXIT_FAILED;
      }
      // An older Runtime refuses a migrated home, so from here a failure
      // keeps the new version instead of rolling back to one that cannot start.
      migrated = migration.state === "cutover";
      await restartUserService(layout, serviceOptions);
      // A restart that returns without error only means launchd/systemd
      // accepted the command. O-22: the first `upgrade` implementation
      // reported success even when the new artifact crash-looped, leaving
      // `current` on a version that never came up. Verify the restarted
      // Runtime actually becomes healthy; if it does not, roll back to the
      // previously working version.
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
        await rollbackUpgrade(parsed.dataDir);
        process.stderr.write(
          `${JSON.stringify(
            {
              schema_version: 1,
              product: PRODUCT,
              command: "upgrade",
              status: "error",
              code: "upgrade_health_check_failed",
              message: "新版本重启后未在限定时间内就绪，已回滚到上一版本。",
            },
            null,
            2,
          )}\n`,
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
      if (!migrated) await rollbackUpgrade(parsed.dataDir);
      process.stderr.write(
        `${JSON.stringify(migrated ? notReadyAfterMigration("upgrade") : serviceErrorDocument("upgrade", error), null, 2)}\n`,
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

/** Apply a confirmed `scan`: ask Runtime to sync one source now.
 *
 * Only a named source can be applied. A bare `--root` plans fine ("all
 * registered roots") but has no Runtime endpoint that scans a raw path, so
 * applying it is refused rather than silently scanning nothing or everything.
 */
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

/** Apply a confirmed `backup create`: ask Runtime to copy its database.
 *
 * `restore` is deliberately not wired: restoring overwrites live data, and
 * doing it behind a bare `--yes` without a verified, restorable artifact would
 * be the kind of destructive default this CLI avoids elsewhere.
 */
async function applyBackupCreate(
  asJson: boolean,
  descriptor: CommandDescriptor | undefined,
  environment: NodeJS.ProcessEnv,
  parsed: { plan: { target: string } },
): Promise<number> {
  const attachment = await attachToRuntime(
    resolveRuntimeCliConfig(environment, process.argv[1]).dataDir,
  );
  if (attachment === null) {
    return runtimeUnavailable(descriptor, asJson);
  }
  try {
    const result = await submitBackup(attachment, parsed.plan.target);
    process.stdout.write(
      asJson
        ? `${JSON.stringify({ schema_version: 1, backup: result })}\n`
        : `target: ${result.target}\nbytes: ${result.bytes}\n`,
    );
    return EXIT_OK;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    // Runtime refusing the target (e.g. `backup_target_outside_data_dir`) is a
    // security rejection, not a malformed argument: the flags were fine. Report
    // it as `rejected`/exit 1 with a machine-readable code, not `usage_error`/
    // exit 2, so a script does not go hunting for an argument typo.
    const code = /^[a-z0-9_.:-]{1,128}$/.test(message)
      ? message
      : "backup_failed";
    if (asJson) {
      process.stderr.write(
        `${JSON.stringify({
          schema_version: 1,
          product: PRODUCT,
          command: "backup",
          verb: "create",
          state: "rejected",
          code,
          message,
          exit_code: EXIT_FAILED,
          next_step:
            "备份目标必须是数据目录内的绝对路径；确认 --target 后重试。",
        })}\n`,
      );
      return EXIT_FAILED;
    }
    process.stderr.write(`备份失败：${message}\n`);
    return EXIT_FAILED;
  }
}

async function applyPath(
  asJson: boolean,
  descriptor: CommandDescriptor | undefined,
  environment: NodeJS.ProcessEnv,
  parsed: { plan: Plan; confirmed: boolean },
): Promise<number> {
  const attachment = await attachToRuntime(
    resolveRuntimeCliConfig(environment, process.argv[1]).dataDir,
  );
  if (attachment === null) {
    return runtimeUnavailable(descriptor, asJson);
  }
  const adding = parsed.plan.action === "add scan root";
  const result = adding
    ? await addScanRoot(attachment, {
        root: parsed.plan.target,
        ...(_sourceOf(parsed.plan.detail) === null
          ? {}
          : { source: _sourceOf(parsed.plan.detail)! }),
        ...(_recursiveOf(parsed.plan.detail) === null
          ? {}
          : { recursive: _recursiveOf(parsed.plan.detail)! }),
      })
    : await removeScanRoot(attachment, { root: parsed.plan.target });
  const failure = _failureOf(result);
  if (failure !== null) {
    return reject(descriptor, asJson, failure);
  }
  process.stdout.write(
    asJson
      ? `${JSON.stringify({ schema_version: 1, path: result }, null, 2)}\n`
      : `${parsed.plan.action}: ${parsed.plan.target}\n`,
  );
  return EXIT_OK;
}

function _sourceArgument(args: readonly string[]): string | null {
  const index = args.indexOf("--source");
  if (index === -1) return null;
  const value = args[index + 1];
  if (value === undefined) return null;
  return value.startsWith("--") ? null : value;
}

function _sourceOf(detail: string | undefined): string | null {
  const match = detail?.match(/source=(\S+)/);
  if (!match || match[1] === "auto") return null;
  return match[1];
}

function _recursiveOf(detail: string | undefined): boolean | null {
  const match = detail?.match(/recursive=(true|false)/);
  return match ? match[1] === "true" : null;
}

function _failureOf(result: unknown): string | null {
  if (result && typeof result === "object" && "_error" in result) {
    return String((result as { _error: unknown })._error);
  }
  return null;
}

async function applyScan(
  asJson: boolean,
  descriptor: CommandDescriptor | undefined,
  environment: NodeJS.ProcessEnv,
  parsed: { plan: { sourceId?: string; all?: boolean } },
): Promise<number> {
  if (parsed.plan.all === true) {
    return await applyScanAll(asJson, descriptor, environment);
  }
  const sourceId = parsed.plan.sourceId;
  if (sourceId === undefined) {
    return reject(
      descriptor,
      asJson,
      "scan 执行必须指定 --source <id>；Runtime 按来源扫描，不接受裸路径。",
    );
  }
  const attachment = await attachToRuntime(
    resolveRuntimeCliConfig(environment, process.argv[1]).dataDir,
  );
  if (attachment === null) {
    return runtimeUnavailable(descriptor, asJson);
  }
  const job = await submitScan(attachment, sourceId);
  if (job === null) {
    // The source cannot be synced — a property of the source, not a failure of
    // this command. Saying so keeps "not synced" distinct from "command failed".
    process.stderr.write(
      asJson
        ? `${JSON.stringify({ schema_version: 1, scan: null, code: "live_sync_unavailable", message: `来源 ${sourceId} 尚未开启持续同步或不支持增量同步。`, next_step: `先运行 memhub sources list 确认来源已连接，再运行 memhub sync resume --source ${sourceId} 并按提示确认。` })}\n`
        : `来源 ${sourceId} 尚未开启持续同步或不支持增量同步。\n下一步：先运行 memhub sources list 确认来源已连接，再运行 memhub sync resume --source ${sourceId} 并按提示确认。\n`,
    );
    return EXIT_FAILED;
  }
  process.stdout.write(
    asJson
      ? `${JSON.stringify({ schema_version: 1, scan: job })}\n`
      : `job_id: ${job.job_id}\nsource_id: ${job.source_id}\nstatus: ${job.status}\n`,
  );
  return EXIT_OK;
}

/** `scan --all`: connect/import every detected source in one batch call.
 *
 * A19. The engine already accepts many source ids in one request, so this is
 * not a per-source loop — a loop would stop at the first failure and lose the
 * per-source receipt. Partial success is reported row by row and returns a
 * non-zero code so a script can tell "some sources are still not imported".
 */
async function applyScanAll(
  asJson: boolean,
  descriptor: CommandDescriptor | undefined,
  environment: NodeJS.ProcessEnv,
): Promise<number> {
  const config = resolveRuntimeCliConfig(environment, process.argv[1]);
  const runtime = await inspectRuntimeStatus({
    dataDir: config.dataDir,
    probeIdentity,
    requireServiceReceipt: true,
  });
  if (runtime.state !== "running") {
    const document: BatchScanDocument = {
      schema_version: 1,
      product: PRODUCT,
      command: "scan",
      state: "rejected",
      request_id: null,
      accepted_source_ids: [],
      sources: [],
      not_found_source_ids: [],
      code: runtime.code,
    };
    process.stderr.write(
      asJson ? `${JSON.stringify(document)}\n` : `未完成：${runtime.code}\n`,
    );
    return runtime.state === "stopped" ? EXIT_RUNTIME_STOPPED : EXIT_FAILED;
  }
  const clients = await attachClients(config.dataDir);
  if (!clients.reader || !clients.operations) {
    const code = clients.failure ?? "runtime_attach_failed";
    const document: BatchScanDocument = {
      schema_version: 1,
      product: PRODUCT,
      command: "scan",
      state: "unknown",
      request_id: null,
      accepted_source_ids: [],
      sources: [],
      not_found_source_ids: [],
      code,
    };
    process.stderr.write(
      asJson ? `${JSON.stringify(document)}\n` : `未完成：${code}\n`,
    );
    return EXIT_FAILED;
  }
  const batch = await planBatchImport(clients.reader);
  if (batch.state !== "preview") {
    const document: BatchScanDocument = {
      schema_version: 1,
      product: PRODUCT,
      command: "scan",
      state: batch.state === "no_sources" ? "no_sources" : batch.state,
      request_id: null,
      accepted_source_ids: [],
      sources: [],
      not_found_source_ids: batch.notFound,
      code: batch.code,
    };
    process.stdout.write(
      asJson ? `${JSON.stringify(document)}\n` : renderBatchScanText(document),
    );
    return batch.state === "no_sources" ? EXIT_OK : EXIT_FAILED;
  }
  const document = await executeBatchImport(
    batch.candidates,
    clients.reader,
    clients.operations,
    batch.notFound,
  );
  process.stdout.write(
    asJson ? `${JSON.stringify(document)}\n` : renderBatchScanText(document),
  );
  return batchScanExitCode(document);
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
}): Promise<SdkMigrationResult> {
  return runSdkMigration({
    dataDir: options.dataDir,
    core: { command: options.coreCommand },
    runtimeBuild: options.runtimeBuild,
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

function sdkMigrationFailure(command: "install" | "upgrade", code: string) {
  return {
    schema_version: 1,
    product: PRODUCT,
    command,
    status: "error",
    code,
    message:
      code === "sdk_migration_disk_space_insufficient"
        ? "磁盘空间不足，记忆数据迁移没有完成，已回滚到上一版本。请腾出与记忆数据同等大小的空间后重试；原有记忆数据不会丢失。"
        : "记忆数据迁移没有完成，已回滚到上一版本。请重试；如果仍然失败，请把这个错误码反馈给我们。原有记忆数据不会丢失。",
  };
}

async function waitForRuntimeReady(
  dataDir: string,
  timeoutMs = 45_000,
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const runtime = await inspectRuntimeStatus({
      dataDir,
      probeIdentity,
      requireServiceReceipt: true,
    }).catch(() => null);
    if (runtimeIsReady(runtime)) return true;
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

async function readDoctorSnapshot(dataDir: string): Promise<DoctorDocument> {
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
  return composeDoctorReport(status, progress);
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
 * (`executeSourceImport`) and the same access endpoint as the flag-driven
 * commands, so the UI/CLI/config state cannot diverge. A source that is picked
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
        display_name: candidate.display_name,
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
    const plan = await planSourceImport(
      clients.reader,
      outcome.answers.sourceIds,
    );
    if (plan.state !== "preview") {
      emitLine(asJson ? `${JSON.stringify(plan)}\n` : renderFlowText(plan));
      return flowExitCode(plan);
    }
    const identity = inspectExecutionPlan(plan.plan);
    if (identity === null || identity.kind !== "source_import") {
      return reject(undefined, asJson, "交互计划无效。");
    }
    const fence = interactiveInitAttemptPath(dataDir, identity.operationId);
    const attempt = await claimPlanAttempt(fence, {
      schema_version: 1,
      operation_id: identity.operationId,
      plan_digest: identity.digest,
    });
    if (attempt === "invalid")
      return reject(undefined, asJson, "交互计划无效。");
    const document =
      attempt === "prior"
        ? await readSourceReceipt(clients.reader, identity.operationId)
        : await executeSourceImport(
            plan.plan,
            clients.reader,
            clients.operations,
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
        CoreUploadOperations &
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

/**
 * `calls clear` / `import-file` are planned and applied from the same parsed
 * invocation: without `--confirm` they only print the plan, and with it they
 * re-derive that exact plan and write. There is no plan file to reconcile.
 */
async function applyUiWriteCommand(
  parsed: UiWriteCommand,
  operations: CoreOperations & CoreUploadOperations & CoreDeleteOperations,
): Promise<UiWriteOutcome> {
  const invalidPlan = (): UiWriteOutcome => ({
    schema_version: 1,
    product: PRODUCT,
    state: "invalid",
    code: "ui_write_plan_invalid",
  });
  switch (parsed.kind) {
    case "calls-clear-plan":
      return planCallEventsClear(parsed.eventIds);
    case "calls-clear-apply": {
      const plan = planCallEventsClear(parsed.eventIds);
      return plan.state === "preview" && plan.plan.kind === "call_events_clear"
        ? executeCallEventsClear(plan.plan, operations)
        : invalidPlan();
    }
    case "calls-clear-all-plan":
      return planCallEventsClearAll();
    case "calls-clear-all-apply": {
      const plan = planCallEventsClearAll();
      return plan.state === "preview" &&
        plan.plan.kind === "call_events_clear_all"
        ? executeCallEventsClearAll(plan.plan, operations)
        : invalidPlan();
    }
    case "import-commit-plan":
      return planImportFileCommit(parsed.uploadId);
    case "import-commit-apply": {
      const plan = planImportFileCommit(parsed.uploadId);
      return plan.state === "preview" && plan.plan.kind === "import_file_commit"
        ? executeImportFileCommit(plan.plan, operations)
        : invalidPlan();
    }
    case "import-discard-plan":
      return planImportFileDiscard(parsed.uploadId);
    case "import-discard-apply": {
      const plan = planImportFileDiscard(parsed.uploadId);
      return plan.state === "preview" &&
        plan.plan.kind === "import_file_discard"
        ? executeImportFileDiscard(plan.plan, operations)
        : invalidPlan();
    }
    case "import-upload-plan": {
      const upload = await readImportFileUpload(parsed.path, parsed.inputKind);
      return upload === null
        ? invalidPlan()
        : {
            schema_version: 1,
            product: PRODUCT,
            state: "preview",
            plan: upload.plan,
          };
    }
    case "import-upload-apply": {
      const upload = await readImportFileUpload(parsed.path, parsed.inputKind);
      return upload === null
        ? invalidPlan()
        : upload.plan.kind === "import_file_upload"
          ? executeImportFileUpload(upload.plan, operations)
          : invalidPlan();
    }
  }
}

/**
 * The second half of the extended UI-write surface. Each branch either prints
 * a plan (no request) or performs exactly one confirmed write; content files
 * and plan files are re-read here so neither a stale file nor a stale plan can
 * change the operation the caller reviewed.
 */
async function applyExtendedWritePlan(
  parsed: ExtendedWriteCommand,
  operations: CoreOperations & CorePutOperations,
): Promise<ExtendedWriteOutcome> {
  const invalidPlan = (): ExtendedWriteOutcome => ({
    schema_version: 1,
    product: PRODUCT,
    state: "invalid",
    code: "ui_write_plan_invalid",
  });
  switch (parsed.kind) {
    case "notes-add-plan": {
      const text = await readMemoryContentFile(parsed.contentFile);
      return text === null ? invalidPlan() : planMemoryNoteAdd(text);
    }
    case "notes-add-apply": {
      const file = await readBoundedJson(parsed.planFile);
      const plan = validateExtendedWritePlan(unwrapPlan(file));
      if (plan === null || plan.kind !== "memory_note_add") {
        return invalidPlan();
      }
      const text = await readMemoryContentFile(parsed.contentFile);
      if (text === null) return invalidPlan();
      const attempt = await claimPlanAttempt(parsed.planFile, {
        schema_version: 1,
        operation_id: plan.request_id,
        plan_digest: plan.digest,
      });
      if (attempt === "invalid") return invalidPlan();
      return executeMemoryNoteAdd(plan, operations, text);
    }
    case "notes-process-plan": {
      return planMemoryNoteProcess(parsed.noteId);
    }
    case "notes-process-apply": {
      const plan = planMemoryNoteProcess(parsed.noteId);
      return plan.state === "preview" &&
        plan.plan.kind === "memory_note_process"
        ? executeMemoryNoteProcess(plan.plan, operations)
        : invalidPlan();
    }
    case "extraction-plan":
      return planExtractionWindowSet(parsed.preset);
    case "extraction-apply": {
      const plan = planExtractionWindowSet(parsed.preset);
      return plan.state === "preview" &&
        plan.plan.kind === "extraction_window_set"
        ? executeExtractionWindowSet(plan.plan, operations)
        : invalidPlan();
    }
    case "graph-plan":
      return planGraphWidthDecide(parsed.decision);
    case "graph-apply": {
      const plan = planGraphWidthDecide(parsed.decision);
      return plan.state === "preview" && plan.plan.kind === "graph_width_decide"
        ? executeGraphWidthDecide(plan.plan, operations)
        : invalidPlan();
    }
    case "extract-manual-plan":
      return planManualExtract(parsed.evidenceIds);
    case "extract-manual-apply": {
      const plan = planManualExtract(parsed.evidenceIds);
      return plan.state === "preview" && plan.plan.kind === "manual_extract"
        ? executeManualExtract(plan.plan, operations)
        : invalidPlan();
    }
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
      // Do not pass the caller's Runtime tuning variables (for example
      // retrieval-index flags) into a maintenance process: a restore must
      // start the product's minimal maintenance configuration, not inherit
      // an unrelated experiment from the shell.
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

type HeadlessCommand =
  | { kind: "setup-plan"; sourceIds: string[] }
  | { kind: "source-import"; planFile: string }
  | { kind: "source-receipt"; requestId: string }
  | { kind: "model-plan"; configFile: string }
  | { kind: "model-configure"; planFile: string; apiKeyFile?: string }
  | { kind: "model-receipt"; operationId: string }
  | { kind: "model-probe-plan"; profileId: string }
  | { kind: "model-probe"; planFile: string }
  | { kind: "model-probe-receipt"; operationId: string }
  | { kind: "model-remove"; profileId: string; confirm: boolean }
  | { kind: "model-activate-plan"; profileId: string }
  | { kind: "model-activate-apply"; profileId: string }
  | { kind: "model-edit-plan"; profileId: string; configFile: string }
  | {
      kind: "model-edit-apply";
      profileId: string;
      configFile: string;
      apiKeyFile?: string;
    };

const OPERATION_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

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

type InitCommand = {
  kind: "init";
  sourceIds: string[] | null;
  allDetected: boolean;
  importHistory: boolean;
  fullRescan: boolean;
  confirmOverrides: boolean;
  dryRun: boolean;
};

/** `init`: first-time onboarding.
 *
 * Three shapes, all ending in the same connect/import call:
 *   - `--source <id>`: scriptable, never prompts (CVM/CI/agent).
 *   - `--all-detected`: connect and import every `found` source, no prompt.
 *   - neither: on a TTY, the three-step wizard (which sources, read/write,
 *     import history); off a TTY, the original candidate list and next command.
 * There is **no separate authorize step** — connecting a source is what grants
 * it, per the 2026-09-26 decision.
 */
function parseInitCommand(rest: readonly string[]): InitCommand | null {
  if (rest.filter((item) => item === "--json").length > 1) return null;
  const tokens = rest.filter((item) => item !== "--json");
  const sourceIds: string[] = [];
  let allDetected = false;
  let importHistory = false;
  let fullRescan = false;
  let confirmOverrides = false;
  let dryRun = false;
  const flags = new Set([
    "--import",
    "--full-rescan",
    "--confirm-overrides",
    "--dry-run",
    "--all-detected",
  ]);
  const seenFlags = new Set<string>();
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (token === "--source") {
      const value = tokens[index + 1];
      if (value === undefined || value.startsWith("--")) return null;
      sourceIds.push(value);
      index += 1;
      continue;
    }
    if (flags.has(token ?? "")) {
      if (seenFlags.has(token!)) return null;
      seenFlags.add(token!);
      if (token === "--import") importHistory = true;
      if (token === "--full-rescan") fullRescan = true;
      if (token === "--confirm-overrides") confirmOverrides = true;
      if (token === "--dry-run") dryRun = true;
      if (token === "--all-detected") allDetected = true;
      continue;
    }
    return null;
  }
  if (sourceIds.length > 32 || new Set(sourceIds).size !== sourceIds.length) {
    return null;
  }
  // Naming both is a contradiction: one names exact sources, the other means
  // "whatever is detected".
  if (allDetected && sourceIds.length > 0) return null;
  return {
    kind: "init",
    // `null` means "list candidates and stop"; an empty array would mean
    // "connect nothing", which is not a thing worth expressing.
    sourceIds: sourceIds.length > 0 ? sourceIds : null,
    allDetected,
    importHistory,
    fullRescan,
    confirmOverrides,
    dryRun,
  };
}

function parseHeadlessCommand(
  command: "setup" | "sources" | "models",
  rest: readonly string[],
): HeadlessCommand | null {
  if (rest.filter((item) => item === "--json").length > 1) return null;
  const tokens = rest.filter((item) => item !== "--json");
  const action = tokens[0];
  if (command === "setup") {
    if (action !== "plan") return null;
    const sourceIds: string[] = [];
    for (let index = 1; index < tokens.length; index += 2) {
      if (
        tokens[index] !== "--source" ||
        tokens[index + 1] === undefined ||
        tokens[index + 1].startsWith("--")
      ) {
        return null;
      }
      sourceIds.push(tokens[index + 1]);
    }
    return sourceIds.length > 0 ? { kind: "setup-plan", sourceIds } : null;
  }
  if (command === "sources") {
    if (action === "receipt" && tokens.length === 2) {
      return OPERATION_ID.test(tokens[1])
        ? { kind: "source-receipt", requestId: tokens[1] }
        : null;
    }
    return action === "import" &&
      tokens.length === 4 &&
      tokens[1] === "--plan-file" &&
      !tokens[2].startsWith("--") &&
      tokens[3] === "--confirm"
      ? { kind: "source-import", planFile: tokens[2] }
      : null;
  }
  if (action === "plan") {
    return tokens.length === 3 &&
      tokens[1] === "--config-file" &&
      !tokens[2].startsWith("--")
      ? { kind: "model-plan", configFile: tokens[2] }
      : null;
  }
  if (action === "receipt" && tokens.length === 2) {
    return OPERATION_ID.test(tokens[1])
      ? { kind: "model-receipt", operationId: tokens[1] }
      : null;
  }
  if (action === "probe-plan" && tokens.length === 2) {
    return OPERATION_ID.test(tokens[1])
      ? { kind: "model-probe-plan", profileId: tokens[1] }
      : null;
  }
  if (action === "probe-receipt" && tokens.length === 2) {
    return OPERATION_ID.test(tokens[1])
      ? { kind: "model-probe-receipt", operationId: tokens[1] }
      : null;
  }
  // `remove` deletes a model profile, so it is two-phase like every other
  // mutating command: without --confirm it only reports what would happen.
  if (action === "remove") {
    if (tokens.length < 2 || !OPERATION_ID.test(tokens[1])) return null;
    let confirmed = false;
    for (let index = 2; index < tokens.length; index += 1) {
      if (tokens[index] === "--confirm" && !confirmed) {
        confirmed = true;
        continue;
      }
      return null;
    }
    return { kind: "model-remove", profileId: tokens[1], confirm: confirmed };
  }
  // Activate takes no request body, so there is no revision Core could enforce
  // as a CAS; the plan names the target and `--confirm` posts once.
  if (action === "activate") {
    if (tokens.length < 2 || !OPERATION_ID.test(tokens[1])) return null;
    if (tokens.length === 2) {
      return { kind: "model-activate-plan", profileId: tokens[1] };
    }
    return tokens.length === 3 && tokens[2] === "--confirm"
      ? { kind: "model-activate-apply", profileId: tokens[1] }
      : null;
  }
  // Edit reuses the `models configure` shape: a bounded config file carries the
  // patch including `expected_revision`; without --confirm it only previews.
  if (action === "edit") {
    if (
      tokens.length < 4 ||
      !OPERATION_ID.test(tokens[1] ?? "") ||
      tokens[2] !== "--config-file" ||
      tokens[3].startsWith("--")
    ) {
      return null;
    }
    let confirmed = false;
    let apiKeyFile: string | undefined;
    for (let index = 4; index < tokens.length; index += 1) {
      const flag = tokens[index];
      if (flag === "--confirm" && !confirmed) {
        confirmed = true;
        continue;
      }
      if (
        flag === "--api-key-file" &&
        apiKeyFile === undefined &&
        tokens[index + 1] !== undefined &&
        !tokens[index + 1].startsWith("--")
      ) {
        apiKeyFile = tokens[index + 1];
        index += 1;
        continue;
      }
      return null;
    }
    if (!confirmed) {
      return tokens.length === 4
        ? {
            kind: "model-edit-plan",
            profileId: tokens[1],
            configFile: tokens[3],
          }
        : null;
    }
    return {
      kind: "model-edit-apply",
      profileId: tokens[1],
      configFile: tokens[3],
      ...(apiKeyFile ? { apiKeyFile } : {}),
    };
  }
  if (action === "probe") {
    return tokens.length === 4 &&
      tokens[1] === "--plan-file" &&
      !tokens[2].startsWith("--") &&
      tokens[3] === "--confirm"
      ? { kind: "model-probe", planFile: tokens[2] }
      : null;
  }
  if (
    action !== "configure" ||
    tokens[1] !== "--plan-file" ||
    tokens[2] === undefined ||
    tokens[2].startsWith("--")
  ) {
    return null;
  }
  let confirm = false;
  let apiKeyFile: string | undefined;
  for (let index = 3; index < tokens.length; index += 1) {
    const flag = tokens[index];
    if (flag === "--confirm" && !confirm) {
      confirm = true;
      continue;
    }
    if (
      flag === "--api-key-file" &&
      apiKeyFile === undefined &&
      tokens[index + 1] !== undefined &&
      !tokens[index + 1].startsWith("--")
    ) {
      apiKeyFile = tokens[index + 1];
      index += 1;
      continue;
    }
    return null;
  }
  return confirm
    ? {
        kind: "model-configure",
        planFile: tokens[2],
        ...(apiKeyFile ? { apiKeyFile } : {}),
      }
    : null;
}

function unwrapPlan(value: unknown): unknown {
  if (
    value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    (value as Record<string, unknown>).state === "preview"
  ) {
    return (value as Record<string, unknown>).plan;
  }
  return value;
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

type UiWriteCommand =
  | { kind: "calls-clear-plan"; eventIds: string[] }
  | { kind: "calls-clear-apply"; eventIds: string[] }
  | { kind: "calls-clear-all-plan" }
  | { kind: "calls-clear-all-apply" }
  | {
      kind: "import-upload-plan";
      path: string;
      inputKind: "raw_conversation" | "memory_content";
    }
  | {
      kind: "import-upload-apply";
      path: string;
      inputKind: "raw_conversation" | "memory_content";
    }
  | { kind: "import-commit-plan"; uploadId: string }
  | { kind: "import-commit-apply"; uploadId: string }
  | { kind: "import-discard-plan"; uploadId: string }
  | { kind: "import-discard-apply"; uploadId: string };

const UI_WRITE_UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/**
 * `memhub calls clear` and `memhub import-file ...`.
 *
 * Every mutating form defaults to a preview that performs no request at all;
 * the same invocation with `--confirm` performs exactly one write. There is no
 * plan file for these because the caller already names the exact target — a
 * second file would add a way to confirm an operation the caller never saw.
 */
function parseUiWriteCommand(
  command: "calls" | "import-file",
  rest: readonly string[],
): UiWriteCommand | null {
  if (rest.filter((item) => item === "--json").length > 1) return null;
  const tokens = rest.filter((item) => item !== "--json");
  const confirm = tokens.includes("--confirm");
  if (tokens.filter((item) => item === "--confirm").length > 1) return null;
  const withoutConfirm = tokens.filter((item) => item !== "--confirm");
  if (command === "calls") {
    if (withoutConfirm[0] !== "clear") return null;
    const all = withoutConfirm.includes("--all");
    const eventIndex = withoutConfirm.indexOf("--event");
    if (all) {
      if (
        eventIndex !== -1 ||
        withoutConfirm.length !== 2 ||
        withoutConfirm.filter((item) => item === "--all").length !== 1
      ) {
        return null;
      }
      return confirm
        ? { kind: "calls-clear-all-apply" }
        : { kind: "calls-clear-all-plan" };
    }
    if (eventIndex === -1) return null;
    const eventIds: string[] = [];
    for (let index = 1; index < withoutConfirm.length; index += 1) {
      const flag = withoutConfirm[index];
      const value = withoutConfirm[index + 1];
      if (flag !== "--event" || value === undefined || value.startsWith("--")) {
        return null;
      }
      if (!UI_WRITE_UUID.test(value)) return null;
      eventIds.push(value);
      index += 1;
    }
    if (eventIds.length === 0 || new Set(eventIds).size !== eventIds.length) {
      return null;
    }
    return confirm
      ? { kind: "calls-clear-apply", eventIds }
      : { kind: "calls-clear-plan", eventIds };
  }
  const action = withoutConfirm[0];
  if (action === "upload") {
    let filePath: string | undefined;
    let inputKind: "raw_conversation" | "memory_content" = "raw_conversation";
    let kindSet = false;
    for (let index = 1; index < withoutConfirm.length; index += 1) {
      const flag = withoutConfirm[index];
      const value = withoutConfirm[index + 1];
      if (value === undefined || value.startsWith("--")) return null;
      if (flag === "--file" && filePath === undefined) filePath = value;
      else if (flag === "--kind" && !kindSet) {
        if (value !== "raw_conversation" && value !== "memory_content") {
          return null;
        }
        inputKind = value;
        kindSet = true;
      } else return null;
      index += 1;
    }
    if (!filePath) return null;
    return confirm
      ? { kind: "import-upload-apply", path: filePath, inputKind }
      : { kind: "import-upload-plan", path: filePath, inputKind };
  }
  if (
    (action === "commit" || action === "discard") &&
    withoutConfirm.length === 2 &&
    UI_WRITE_UUID.test(withoutConfirm[1] ?? "")
  ) {
    const uploadId = withoutConfirm[1]!;
    if (action === "commit") {
      return confirm
        ? { kind: "import-commit-apply", uploadId }
        : { kind: "import-commit-plan", uploadId };
    }
    return confirm
      ? { kind: "import-discard-apply", uploadId }
      : { kind: "import-discard-plan", uploadId };
  }
  return null;
}

type ExtendedWriteCommand =
  | { kind: "notes-add-plan"; contentFile: string }
  | { kind: "notes-add-apply"; contentFile: string; planFile: string }
  | { kind: "notes-process-plan"; noteId: string }
  | { kind: "notes-process-apply"; noteId: string }
  | { kind: "extraction-plan"; preset: string }
  | { kind: "extraction-apply"; preset: string }
  | { kind: "graph-plan"; decision: string }
  | { kind: "graph-apply"; decision: string }
  | { kind: "extract-manual-plan"; evidenceIds: string[] }
  | { kind: "extract-manual-apply"; evidenceIds: string[] };

function parseExtendedWriteCommand(
  command: "notes" | "extraction" | "graph",
  rest: readonly string[],
): ExtendedWriteCommand | null {
  if (rest.filter((item) => item === "--json").length > 1) return null;
  const tokens = rest.filter((item) => item !== "--json");
  const confirmed = tokens.includes("--confirm");
  if (tokens.filter((item) => item === "--confirm").length > 1) return null;
  const t = tokens.filter((item) => item !== "--confirm");
  if (command === "notes") {
    if (t[0] === "add") {
      let contentFile: string | undefined;
      for (let index = 1; index < t.length; index += 2) {
        const flag = t[index];
        const value = t[index + 1];
        if (value === undefined || value.startsWith("--")) return null;
        if (flag === "--content-file" && contentFile === undefined)
          contentFile = value;
        else return null;
      }
      if (!contentFile || confirmed) return null;
      return { kind: "notes-add-plan", contentFile };
    }
    if (t[0] === "apply") {
      let contentFile: string | undefined;
      let planFile: string | undefined;
      for (let index = 1; index < t.length; index += 2) {
        const flag = t[index];
        const value = t[index + 1];
        if (value === undefined || value.startsWith("--")) return null;
        if (flag === "--content-file" && contentFile === undefined)
          contentFile = value;
        else if (flag === "--plan-file" && planFile === undefined)
          planFile = value;
        else return null;
      }
      return confirmed && contentFile && planFile
        ? { kind: "notes-add-apply", contentFile, planFile }
        : null;
    }
    if (
      t[0] === "process" &&
      t.length === 2 &&
      UI_WRITE_UUID.test(t[1] ?? "")
    ) {
      return confirmed
        ? { kind: "notes-process-apply", noteId: t[1]! }
        : { kind: "notes-process-plan", noteId: t[1]! };
    }
    return null;
  }
  if (command === "extraction") {
    if (t[0] !== "set" || t.length !== 3 || t[1] !== "--window") return null;
    if (!["3m", "6m", "1y", "all"].includes(t[2]!)) return null;
    return confirmed
      ? { kind: "extraction-apply", preset: t[2]! }
      : { kind: "extraction-plan", preset: t[2]! };
  }
  if (t[0] !== "width" || t.length !== 3 || t[1] !== "--decision") return null;
  if (!["keep_existing", "rebuild_with_new", "export_first"].includes(t[2]!)) {
    return null;
  }
  return confirmed
    ? { kind: "graph-apply", decision: t[2]! }
    : { kind: "graph-plan", decision: t[2]! };
}

function parseManualExtractCommand(
  rest: readonly string[],
): ExtendedWriteCommand | null {
  if (rest.filter((item) => item === "--json").length > 1) return null;
  const tokens = rest.filter((item) => item !== "--json");
  const confirmed = tokens.includes("--confirm");
  const t = tokens.filter((item) => item !== "--confirm");
  if (t[0] !== "extract") return null;
  const evidenceIds: string[] = [];
  for (let index = 1; index < t.length; index += 2) {
    const flag = t[index];
    const value = t[index + 1];
    if (
      flag !== "--evidence" ||
      value === undefined ||
      value.startsWith("--")
    ) {
      return null;
    }
    if (!UI_WRITE_UUID.test(value)) return null;
    evidenceIds.push(value);
  }
  if (new Set(evidenceIds).size !== evidenceIds.length) return null;
  return confirmed
    ? { kind: "extract-manual-apply", evidenceIds }
    : { kind: "extract-manual-plan", evidenceIds };
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
  | { kind: "show"; memoryId: string }
  | {
      kind: "update";
      memoryId: string;
      contentFile: string;
      confirmed: boolean;
    }
  | { kind: "delete"; memoryId: string; confirmed: boolean }
  | {
      kind: "plan-add";
      contentFile: string;
      memoryKind: string;
      scopes: string[];
      evidenceIds: string[];
    }
  | {
      kind: "plan-existing";
      operation: "correct" | "delete";
      memoryId: string;
      contentFile?: string;
    }
  | { kind: "apply"; planFile: string; contentFile?: string };

async function parseMemoryCommand(
  rest: readonly string[],
): Promise<MemoryCommand | null> {
  if (rest.filter((item) => item === "--json").length > 1) return null;
  const tokens = rest.filter((item) => item !== "--json");
  const action = tokens[0];
  if (action === "show") {
    const memoryId = tokens[1];
    return tokens.length === 2 && memoryId && isMemoryId(memoryId)
      ? { kind: "show", memoryId }
      : null;
  }
  if (action === "update" || action === "delete") {
    const memoryId = tokens[1];
    if (!memoryId || !isMemoryId(memoryId)) return null;
    let contentFile: string | undefined;
    let confirmed = false;
    for (let index = 2; index < tokens.length; index += 1) {
      const flag = tokens[index];
      if (flag === "--confirm") {
        if (confirmed) return null;
        confirmed = true;
        continue;
      }
      const value = tokens[index + 1];
      if (value === undefined || value.startsWith("--")) return null;
      if (flag !== "--content-file" || contentFile !== undefined) return null;
      contentFile = value;
      index += 1;
    }
    if (action === "update")
      return contentFile
        ? { kind: "update", memoryId, contentFile, confirmed }
        : null;
    return contentFile === undefined
      ? { kind: "delete", memoryId, confirmed }
      : null;
  }
  if (action === "plan" || action === "apply") return null;
  if (action === "plan") return parseMemoryPlanCommand(tokens.slice(1));
  if (action === "apply") {
    let planFile: string | undefined;
    let contentFile: string | undefined;
    let confirmed = false;
    for (let index = 1; index < tokens.length; index += 1) {
      const flag = tokens[index];
      if (flag === "--confirm") {
        if (confirmed) return null;
        confirmed = true;
        continue;
      }
      const value = tokens[index + 1];
      if (value === undefined || value.startsWith("--")) return null;
      if (flag === "--plan-file" && planFile === undefined) planFile = value;
      else if (flag === "--content-file" && contentFile === undefined)
        contentFile = value;
      else return null;
      index += 1;
    }
    return planFile && confirmed
      ? { kind: "apply", planFile, ...(contentFile ? { contentFile } : {}) }
      : null;
  }
  if (action !== "list") return null;
  let status: string | undefined;
  let offset = 0;
  let limit = 100;
  let offsetSet = false;
  let limitSet = false;
  for (let index = 1; index < tokens.length; index += 2) {
    const flag = tokens[index];
    const value = tokens[index + 1];
    if (value === undefined || value.startsWith("--")) return null;
    if (flag === "--status" && status === undefined) status = value;
    else if (flag === "--limit" && !limitSet) {
      limit = Number(value);
      limitSet = true;
    } else if (flag === "--offset" && !offsetSet) {
      offset = Number(value);
      offsetSet = true;
    } else return null;
  }
  if (status !== undefined && (status.length === 0 || status.length > 64))
    return null;
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 500) return null;
  if (!Number.isSafeInteger(offset) || offset < 0) return null;
  return { kind: "list", query: { status, offset, limit } };
}

function parseMemoryPlanCommand(
  tokens: readonly string[],
): MemoryCommand | null {
  const operation = tokens[0];
  const add = operation === "add";
  if (!add && !["correct", "delete"].includes(operation)) {
    return null;
  }
  const memoryId = add ? undefined : tokens[1];
  if (!add && (!memoryId || !isMemoryId(memoryId))) return null;
  let contentFile: string | undefined;
  let memoryKind = "chat";
  let kindSet = false;
  const scopes: string[] = [];
  const evidenceIds: string[] = [];
  for (let index = add ? 1 : 2; index < tokens.length; index += 1) {
    const flag = tokens[index];
    const value = tokens[index + 1];
    if (value === undefined || value.startsWith("--")) return null;
    if (flag === "--content-file" && contentFile === undefined)
      contentFile = value;
    else if (flag === "--kind" && add && !kindSet) {
      memoryKind = value;
      kindSet = true;
    } else if (flag === "--scope" && add && !scopes.includes(value))
      scopes.push(value);
    else if (flag === "--evidence" && add && !evidenceIds.includes(value))
      evidenceIds.push(value);
    else return null;
    index += 1;
  }
  if (add) {
    return contentFile && scopes.length > 0 && evidenceIds.length > 0
      ? {
          kind: "plan-add",
          contentFile,
          memoryKind,
          scopes,
          evidenceIds,
        }
      : null;
  }
  if (
    (operation === "correct" && !contentFile) ||
    (operation === "delete" && (contentFile !== undefined || scopes.length > 0))
  ) {
    return null;
  }
  return {
    kind: "plan-existing",
    operation: operation as "correct" | "delete",
    memoryId: memoryId as string,
    ...(contentFile ? { contentFile } : {}),
  };
}

async function readMemoryContentFile(file: string): Promise<string | null> {
  const bytes = await readBoundedFile(file, 100_000 * 4 + 2);
  return bytes ? parseMemoryContent(bytes) : null;
}

function emitMemoryFlow(document: MemoryFlowDocument, asJson: boolean): void {
  emit(
    asJson ? `${JSON.stringify(document)}\n` : renderMemoryFlowText(document),
  );
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
  const descriptor = findCommand(requested);
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
    process.stderr.write(
      `${JSON.stringify({
        schema_version: 1,
        product: PRODUCT,
        command: command?.name,
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

/** Parse `delete-data`: preview by default; `--yes` alone still only previews
 * unless `--confirm <path>` repeats the exact directory. */
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

/** Where `init --source <id> --import` keeps its durable attempt fence.
 *
 * It must be per-data-dir (two installs must not share it) and per-operation
 * (a later, distinct import is a new operation, not a replay). Putting it under
 * the data dir also keeps it out of the user's current working directory, where
 * a literal `init` path could collide with unrelated files or another run.
 */
export function initPlanAttemptPath(
  dataDir: string,
  operationId: string,
): string {
  // Keep the fence directly under the data dir, which the Runtime already
  // proved is writable (it holds the database and receipt files). A nested
  // directory added a second failure mode: if it could not be created, a
  // valid import degraded into a bare usage error instead of running.
  return path.join(dataDir, `.cli-init-attempt-${operationId}`);
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
