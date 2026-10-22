export {
  DOMAIN_CODES,
  type DomainCode,
  DomainError,
  diagnosticDomainError,
  domainCodeOf,
  isDomainCode,
  isDomainError,
} from "@one-memory/host-bridge";
export {
  type BackgroundHealth,
  inspectBackgroundHealth,
} from "./background-health.js";
export {
  type BackgroundRegistration,
  type BackgroundRegistrationStatus,
  inspectBackgroundRegistration,
  type LegacyBackgroundRegistration,
} from "./background-status.js";
export {
  inspectRecallBrokerStatus,
  type RecallBrokerStatus,
} from "./broker-status.js";
export {
  type CliLinkPlan,
  type CliLinkReceipt,
  installCliLink,
  planCliLink,
  rollbackCliLink,
} from "./cli-link.js";
export {
  type ConnectionClient,
  type ConnectionClientResult,
  createConnectionClient,
} from "./connection-client.js";
export {
  type CoreLaunch,
  CoreSupervisor,
  type CoreSupervisorOptions,
  findAvailablePort,
  isOwnedHealth,
  processIsAlive,
} from "./core-supervisor.js";
export {
  DIAGNOSTIC_MAX_AGE_MS,
  DIAGNOSTIC_MAX_FILE_BYTES,
  DIAGNOSTIC_MAX_TOTAL_BYTES,
  type DiagnosticEventV1,
  type DiagnosticInput,
  type DiagnosticLevel,
  DiagnosticLogger,
  type DiagnosticLoggerOptions,
  type DiagnosticMarker,
  type DiagnosticService,
  type DiagnosticSink,
  diagnosticError,
  diagnosticEvent,
  diagnosticLogFiles,
  diagnosticZipFilename,
  exportDiagnosticBundle,
  exportDiagnosticZip,
  pruneDiagnosticLogs,
  readDiagnosticEvents,
  recordDiagnosticMarker,
  writeDiagnosticEventSync,
} from "./diagnostic-log.js";
export {
  discoverRuntimeAttachment,
  type RuntimeAttachment,
  type RuntimeAttachmentOptions,
  RuntimeDiscoveryError,
  type RuntimeIdentity,
  readPublishedCorePort,
} from "./discovery.js";
export {
  createHostPermissionClient,
  type HostPermissionClient,
} from "./host-control-client.js";
export {
  HostPermissions,
  LOCAL_EXPERIENCE_UNAVAILABLE,
  type RecallPermissionStatus,
  type RevokeRecallPermissionInput,
  type SetRecallPermissionInput,
} from "./host-permissions.js";
export {
  applyLocalInstall,
  applyLocalUninstall,
  assertDistributionProtocolCompatibility,
  type DistributionManifest,
  type LocalInstallPlan,
  type LocalInstallReceipt,
  type LocalUninstallPlan,
  type LocalUninstallReceipt,
  planLocalInstall,
  planLocalUninstall,
  rollbackLocalInstall,
} from "./local-install.js";
export { installPackagedRuntime } from "./packaged-runtime-install.js";
export {
  type RecallBrokerFactory,
  RecallBrokerSupervisor,
  type RecallBrokerSupervisorOptions,
} from "./recall-broker-supervisor.js";
export {
  isHostPresent,
  type RecallHostDescriptor,
  recallHosts,
} from "./recall-hosts.js";
export {
  CLI_PROTOCOL,
  CLI_SUPPORTED_RUNTIME_PROTOCOL,
  type ProtocolRange,
  protocolIncludes,
  protocolRange,
  RUNTIME_MANIFEST_SCHEMA,
  RUNTIME_PROTOCOL,
  RUNTIME_SUPPORTED_CLI_PROTOCOL,
} from "./release-contract.js";
export {
  type AcquiredRuntimeDistribution,
  acquireRuntimeDistribution,
  type DesktopReleaseArtifact,
  type HttpGet,
  type HttpGetResponse,
  type MetadataReleaseArtifact,
  parseRuntimeReleaseManifest,
  type ReleaseArtifact,
  type ReleaseChannel,
  type ResolvedRuntimeRelease,
  type RuntimeReleaseArtifact,
  type RuntimeReleaseManifest,
  readReleaseManifestFile,
  resolveRuntimeRelease,
  type StatfsAvailable,
  selectRuntimeReleaseArtifact,
  sha256File,
} from "./release-download.js";
export {
  cleanupRemovedAppOwnership,
  createRuntimeService,
  type RuntimeCliConfig,
  resolveRuntimeCliConfig,
  runRuntimeCli,
} from "./runtime-cli.js";
export {
  type EnsureRuntimeOptions,
  ensureRuntimeProcess,
  type RuntimeLaunch,
  type StopRuntimeOptions,
  stopRuntimeProcess,
  type WaitForRuntimeOptions,
  waitForRuntimeProcess,
} from "./runtime-launcher.js";
export {
  type RuntimeMaintenanceDependencies,
  withRuntimeMaintenanceFence,
} from "./runtime-maintenance.js";
export {
  type ManagedRuntime,
  RuntimeService,
  type RuntimeServiceOptions,
} from "./runtime-service.js";
export {
  inspectRuntimeStatus,
  type RuntimeStatus,
  type RuntimeStatusState,
} from "./runtime-status.js";
export {
  type RuntimeConnection,
  type RuntimeCore,
  type RuntimeSidecar,
  RuntimeSupervisor,
  type RuntimeSupervisorOptions,
} from "./runtime-supervisor.js";
export {
  installUserService,
  parseDisabledOverride,
  restartUserService,
  type ServiceCommandRunner,
  type ServiceRegistrationInspector,
  type ServiceRegistrationState,
  uninstallUserService,
} from "./service-install.js";
export {
  appOwnedRuntimeShouldStop,
  claimRuntimeServiceForApp,
  claimRuntimeServiceForCli,
  disableRuntimeService,
  enableRuntimeService,
  type RuntimeServiceOwner,
  readRuntimeServiceOwner,
  removeRuntimeServiceOwner,
  runtimeServiceEnabled,
  runtimeServiceEnableMarkerPath,
  runtimeServiceOwnerPath,
  setAppRuntimeStartAtLogin,
  writeRuntimeServiceOwner,
} from "./service-ownership.js";
export {
  canonicalManagedServiceRoot,
  defaultManagedInstallRoot,
  hourlyIsolationFromEnvironment,
  planUserService,
  RUNTIME_SERVICE_ID,
  type RuntimeInstallLayout,
  type RuntimePlatform,
  type RuntimeServicePlan,
  runtimeInstallLayout,
  stableRuntimeInstallRoot,
} from "./service-plan.js";
export { createSourceChangeHandler } from "./source-change-handler.js";
export {
  type ApplySyncHookInput,
  hasSyncHookArtifacts,
  loadSyncHookAssets,
  orphanSyncHookIdentity,
  type SyncHookPreview,
  type SyncHookStatus,
  syncHookInput,
  syncHookPreview,
} from "./sync-hook.js";
