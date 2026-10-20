import { translate, type UiMessage } from "@one-memory/contracts";
import { RefreshCw, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { ConnectionFallback } from "./ConnectionFallback";
import type {
  RecallHostStatus,
  RecallInstallPreview,
  RecallLinkCheck,
  RecallUninstallPreview,
} from "./global";
import { HistoryImport } from "./HistoryImport";
import {
  applyHostSkill,
  grantHostPermission,
  hostBridgeAvailable,
  hostCapabilities,
  hostReasonLabel,
  hostWriteReason,
  installHost,
  previewHostInstall,
  previewHostSkill,
  previewHostUninstall,
  probeHost,
  readHostPermission,
  refreshRecallHosts,
  uninstallHost,
  useRecallHosts,
} from "./host-state";
import { RecallPermissionControl } from "./RecallPermissionControl";
import { fullGrantSelection, selectionIsValid } from "./recall-grant";
import { LINK_WORDING, SKILL_WORDING } from "./recall-host-wording";
import { SourceDirectoryPicker } from "./SourceDirectoryPicker";
import { SourceImportReceipt } from "./SourceImportReceipt";
import { SourceJobActions } from "./SourceJobActions";
import { SourceSnapshotStatus } from "./SourceSnapshotStatus";
import { SyncHookControl } from "./SyncHookControl";
import { useTranslate, useUiLanguage } from "./ui-language";
import { traceUiAction, type UiTrace } from "./ui-trace";
import {
  isDefiniteAccessRefusal,
  type useImportSources,
} from "./use-import-sources";

type ImportState = ReturnType<typeof useImportSources>;

/** One app's part of a bulk connect: what installing it writes, and whether a
 * grant still has to be written after that. */
type BulkStep = {
  hostId: string;
  label: string;
  /** Null when the connection is already in place. */
  install: RecallInstallPreview | null;
  allow: boolean;
  /** Non-null when this host could not be prepared and will be skipped. */
  error: string | null;
};

function requiresHookRemoval(error: unknown) {
  return (
    error instanceof Error && /\bsync_hook_remove_first\b/.test(error.message)
  );
}

function needsRecallReconnect(host: RecallHostStatus) {
  return (
    host.permission?.state === "relink-required" ||
    host.permission?.state === "revoked" ||
    host.permission?.reason === "installation_incomplete"
  );
}

export function SourcesView({ imports }: { imports: ImportState }) {
  const t = useTranslate();
  const language = useUiLanguage();
  const visibleSources = useMemo(
    () =>
      imports.sources.filter(
        (source) =>
          source.available ||
          source.authorized ||
          source.candidate_change_allowed === true,
      ),
    [imports.sources],
  );
  const availableUnconnected = useMemo(
    () =>
      visibleSources.filter((source) => source.available && !source.authorized),
    [visibleSources],
  );
  const [pendingProcessingSource, setPendingProcessingSource] = useState<
    string | null
  >(null);
  const [directoryBusy, setDirectoryBusy] = useState<string | null>(null);
  const [consentPhase, setConsentPhase] = useState<
    "general" | "environment_override" | null
  >(null);
  const [consentSources, setConsentSources] = useState<ImportState["sources"]>(
    [],
  );
  // The trace opened by the switch, carried through the consent dialog so the
  // confirmation's connect request joins the same four-line chain instead of
  // emitting a second, kernel-less pair. The trace object (not just its id) is
  // kept so the dialog can close it exactly once when it resolves.
  const consentTrace = useRef<UiTrace | null>(null);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [retryFailedConnection, setRetryFailedConnection] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [authorizingSource, setAuthorizingSource] = useState<string | null>(
    null,
  );
  const [authorizationErrors, setAuthorizationErrors] = useState<
    Record<string, string>
  >({});
  const connectionFlight = useRef(false);
  const consentPanel = useRef<HTMLElement>(null);
  const consentOpen = consentPhase !== null;
  const consentConnectionError =
    connectionError ===
    "来源状态或路径已变化，请取消后重新核对。未提交本次授权。"
      ? t("来源状态或路径已变化，请取消后重新核对。未提交本次授权。")
      : connectionError === "所选来源未获准导入，请核对分项原因后重试。"
        ? t("所选来源未获准导入，请核对分项原因后重试。")
        : connectionError === "连接结果尚未确认。所选来源已保留，请核对后重试。"
          ? t("连接结果尚未确认。所选来源已保留，请核对后重试。")
          : connectionError;
  const hostRead = useRecallHosts({ read: true });
  // A failed host action shows the list as unreadable until the next read lands.
  const [hostActionFailed, setHostActionFailed] = useState(false);
  const recallHosts: RecallHostStatus[] | "checking" | "error" | "unavailable" =
    !hostBridgeAvailable()
      ? "unavailable"
      : hostActionFailed || (hostRead.isError && !hostRead.data)
        ? "error"
        : hostRead.data
          ? hostRead.data.filter((host) => host.present)
          : "checking";
  const markHostsUnreadable = () => setHostActionFailed(true);
  const rereadHosts = async () => {
    await refreshRecallHosts();
    setHostActionFailed(false);
  };
  const [busyHost, setBusyHost] = useState<string | null>(null);
  const [hostErrors, setHostErrors] = useState<Record<string, string>>({});
  const [hookRemovalRequired, setHookRemovalRequired] = useState<string | null>(
    null,
  );
  const [linkCheck, setLinkCheck] = useState<Record<string, RecallLinkCheck>>(
    {},
  );
  const [skillConflict, setSkillConflict] = useState<{
    hostId: string;
    fingerprint: string;
  } | null>(null);
  const [installPreview, setInstallPreview] =
    useState<RecallInstallPreview | null>(null);
  const [uninstallPreview, setUninstallPreview] =
    useState<RecallUninstallPreview | null>(null);
  const [bulk, setBulk] = useState<BulkStep[] | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkErrors, setBulkErrors] = useState<Record<string, string>>({});
  const [bulkDone, setBulkDone] = useState<string[] | null>(null);

  useEffect(() => {
    if (!consentOpen || !consentPanel.current) return;
    const before = document.activeElement;
    const siblings = new Map<HTMLElement, boolean>();
    let branch: HTMLElement =
      consentPanel.current.parentElement ?? consentPanel.current;
    while (branch.parentElement) {
      for (const sibling of branch.parentElement.children) {
        if (!(sibling instanceof HTMLElement) || sibling === branch) continue;
        siblings.set(sibling, sibling.inert);
        sibling.inert = true;
      }
      branch = branch.parentElement;
      if (branch === document.body) break;
    }
    consentPanel.current.querySelector<HTMLButtonElement>("button")?.focus();
    return () => {
      for (const [sibling, wasInert] of siblings) sibling.inert = wasInert;
      if (before instanceof HTMLElement && before.isConnected) before.focus();
    };
  }, [consentOpen]);

  useEffect(() => {
    if (connecting) consentPanel.current?.focus();
  }, [connecting]);

  const connected = visibleSources.filter((source) => source.authorized);
  const syncable = connected.filter(
    (source) =>
      source.available &&
      source.sync_enabled === true &&
      typeof source.grant_revision === "number" &&
      typeof source.root_id === "string" &&
      source.capabilities.live_sync === "implemented" &&
      source.sync_completeness !== "one_time_snapshot",
  );
  const needsConnection = availableUnconnected.length > 0;
  const actionBusy =
    connecting ||
    authorizingSource !== null ||
    directoryBusy !== null ||
    imports.busyKey === "connect:selected" ||
    imports.busyKey === "sync:all";
  const snapshotStale = imports.sourceSnapshot?.stale === true;
  const overrideSources = consentSources.filter(
    (source) => source.candidate_kind !== "default",
  );

  async function connectApproved() {
    if (connectionFlight.current || !consentSources.length) return;
    if (
      consentSources.some(
        (approved) =>
          !imports.sources.some(
            (current) =>
              current.source_id === approved.source_id &&
              current.available &&
              current.authorized === approved.authorized &&
              current.candidate_kind === approved.candidate_kind &&
              (current.candidate_revision ?? 0) ===
                (approved.candidate_revision ?? 0) &&
              current.candidate_path === approved.candidate_path,
          ),
      )
    ) {
      setConnectionError(
        "来源状态或路径已变化，请取消后重新核对。未提交本次授权。",
      );
      return;
    }
    connectionFlight.current = true;
    setConnecting(true);
    setConnectionError(null);
    try {
      const result = await imports.connectSelected(
        consentSources.map((source) => source.source_id),
        consentSources,
        {
          ...(retryFailedConnection ? { retryFailed: true } : {}),
          ...(consentTrace.current
            ? { traceId: consentTrace.current.traceId }
            : {}),
        },
      );
      if (!result.accepted_source_ids.length) {
        setRetryFailedConnection(true);
        setConnectionError("所选来源未获准导入，请核对分项原因后重试。");
        return;
      }
      setConsentPhase(null);
    } catch {
      setConnectionError("连接结果尚未确认。所选来源已保留，请核对后重试。");
      consentTrace.current?.done("unknown", { reason: "connect_unconfirmed" });
      consentTrace.current = null;
    } finally {
      connectionFlight.current = false;
      setConnecting(false);
    }
  }

  /** The one Harness switch: scan + enqueue + schedule, or freeze all three. */
  async function toggleSourceProcessing(
    source: ImportState["sources"][number],
    enabled: boolean,
  ) {
    if (enabled && !source.available && !source.authorized) return;
    setConnectionError(null);
    setRetryFailedConnection(false);
    setAuthorizationErrors((errors) => {
      if (!(source.source_id in errors)) return errors;
      const next = { ...errors };
      delete next[source.source_id];
      return next;
    });
    if (snapshotStale) {
      setConnectionError(
        "来源状态待核对，不能修改处理开关。请先刷新来源状态。",
      );
      return;
    }
    setPendingProcessingSource(source.source_id);
    const trace = traceUiAction("sources", "toggle");
    if (source.authorized) {
      try {
        await imports.setSync(source, enabled, trace.traceId);
        trace.done("succeeded");
      } catch (error) {
        const refused = isDefiniteAccessRefusal(error);
        trace.done(refused ? "failed" : "unknown", {
          reason: refused
            ? enabled
              ? "processing_on_failed"
              : "processing_freeze_failed"
            : "processing_result_unconfirmed",
        });
        setAuthorizationErrors((errors) => ({
          ...errors,
          [source.source_id]: refused
            ? enabled
              ? t("未能开启处理，请核对后重试。")
              : t("未能冻结处理，请核对后重试。")
            : t("处理开关的结果尚未确认，请刷新核对；不会自动重试。"),
        }));
      } finally {
        setPendingProcessingSource(null);
      }
      return;
    }
    if (!enabled) {
      setPendingProcessingSource(null);
      trace.done("succeeded");
      return;
    }
    setConsentSources([{ ...source }]);
    consentTrace.current = trace;
    if (source.candidate_kind === "environment_override") {
      setPendingProcessingSource(null);
      setConsentPhase("environment_override");
      return;
    }
    setAuthorizingSource(source.source_id);
    setConnecting(true);
    try {
      const result = await imports.connectSelected(
        [source.source_id],
        [{ ...source }],
        { traceId: trace.traceId },
      );
      if (!result.accepted_source_ids.includes(source.source_id)) {
        trace.done("failed", { reason: "connect_not_accepted" });
        setRetryFailedConnection(true);
        const failure = result.failed_sources[source.source_id];
        setAuthorizationErrors((errors) => ({
          ...errors,
          [source.source_id]: failure
            ? t("未能开启处理：{reason}", {
                reason: failureLabel(failure, language),
              })
            : t("所选来源未获准导入，请核对后重试。"),
        }));
        setConnectionError(
          result.failed_sources[source.source_id]
            ? t("未能开启处理：{reason}", {
                reason: failureLabel(
                  result.failed_sources[source.source_id],
                  language,
                ),
              })
            : "所选来源未获准导入，请核对分项原因后重试。",
        );
      } else {
        trace.done("succeeded", { count: 1 });
      }
    } catch {
      // The reason string already says `unknown`; the outcome must match, or the
      // log claims a failure the client cannot know about.
      trace.done("unknown", { reason: "connect_result_unknown" });
      setRetryFailedConnection(true);
      const failure = t("连接结果尚未确认，请核对后重试。");
      setAuthorizationErrors((errors) => ({
        ...errors,
        [source.source_id]: failure,
      }));
      setConnectionError(t("未能开启处理：{reason}", { reason: failure }));
    } finally {
      setAuthorizingSource(null);
      setPendingProcessingSource(null);
      setConnecting(false);
    }
  }

  const hostList = Array.isArray(recallHosts) ? recallHosts : [];
  /** Hosts a single bulk connect still has work to do on. */
  const bulkTargets = hostList.filter(
    (host) =>
      !host.installed ||
      needsRecallReconnect(host) ||
      host.permission?.receipt?.status === "install-only",
  );
  const hostBusy = busyHost !== null || bulkBusy;

  /** Allow one host everything on offer, exactly as its sheet would.
   *
   * The installation is re-read immediately before the write, so a connection
   * that changed between the preview and the write is reported rather than
   * overwritten with an approval the user gave for something else.
   */
  async function allowHost(hostId: string) {
    // An older desktop shell may not carry the permission bridge at all; then
    // the install still happened and the grant is simply left to the row.
    if (!hostCapabilities().permission) return;
    const status = await readHostPermission(hostId);
    const receipt = status?.receipt;
    if (!receipt || status?.reason !== null) return;
    if (status.state !== "install-only" || receipt.status === "approved")
      return;
    const selection = fullGrantSelection({
      sources: imports.sources,
      keep: receipt.allowedTools,
    });
    if (!selectionIsValid(selection)) return;
    await grantHostPermission({
      hostId,
      input: {
        installationId: receipt.installationId,
        expectedRevision: receipt.revision,
        allowedTools: selection.allowedTools,
        allowedScopes: selection.allowedScopes,
        userConfirmed: true,
      },
    });
  }

  /** Read what connecting every app would write, before writing any of it. */
  async function prepareBulk() {
    if (!window.oneMemory || bulkBusy || !bulkTargets.length) return;
    setBulkBusy(true);
    setBulkDone(null);
    setBulkErrors({});
    try {
      const steps: BulkStep[] = [];
      for (const host of bulkTargets) {
        const needsInstall = !host.installed || needsRecallReconnect(host);
        let install: RecallInstallPreview | null = null;
        let error: string | null = null;
        if (needsInstall) {
          try {
            install = await previewHostInstall(host.hostId);
          } catch (reason) {
            error = requiresHookRemoval(reason)
              ? "请先移除这个应用的同步 Hook"
              : "未能读取这个应用的连接变更预览";
          }
        }
        steps.push({
          hostId: host.hostId,
          label: host.label,
          install,
          allow: true,
          error,
        });
      }
      setBulk(steps);
    } finally {
      setBulkBusy(false);
    }
  }

  /** Install and allow every prepared host, one at a time.
   *
   * Sequential rather than parallel: these are other programs' settings files,
   * and a half-finished batch is easier to describe when the writes are
   * ordered and each result is known before the next one starts.
   */
  async function runBulk() {
    if (!window.oneMemory || !bulk || bulkBusy) return;
    setBulkBusy(true);
    const errors: Record<string, string> = {};
    const done: string[] = [];
    try {
      for (const step of bulk) {
        if (step.error) continue;
        setBusyHost(step.hostId);
        try {
          if (step.install)
            await installHost({
              hostId: step.hostId,
              fingerprint: step.install.fingerprint,
            });
          if (step.allow) await allowHost(step.hostId);
          done.push(step.hostId);
        } catch (reason) {
          errors[step.hostId] = requiresHookRemoval(reason)
            ? "请先移除这个应用的同步 Hook"
            : (hostWriteReason(reason) ?? "未能完成");
        } finally {
          setBusyHost(null);
        }
      }
      // Installed state and every receipt live on the desktop, so the whole
      // list is re-read rather than patched from what this run thinks it did.
      await rereadHosts();
    } catch {
      markHostsUnreadable();
    } finally {
      setBulkErrors(errors);
      setBulkDone(done);
      setBulkBusy(false);
    }
  }

  return (
    <section className="view sources-view" aria-labelledby="sources-title">
      <header className="view-header">
        <div>
          <h1 id="sources-title">{t("应用")}</h1>
          <p className="lede">{t("管理对话来源与记忆使用权限。")}</p>
        </div>
        <button
          type="button"
          disabled={
            actionBusy ||
            snapshotStale ||
            (needsConnection ? true : !syncable.length)
          }
          onClick={() => {
            if (!needsConnection) {
              void imports.syncAll();
            }
          }}
        >
          <RefreshCw size={16} aria-hidden="true" />
          {actionBusy
            ? t("正在开始…")
            : needsConnection
              ? t("逐个开启来源授权")
              : t("同步来源")}
        </button>
      </header>

      <SourceSnapshotStatus
        snapshot={imports.sourceSnapshot}
        onRefresh={imports.refresh}
      />

      <div className="history-import-row" data-tour-id="history-import-row">
        <HistoryImport
          onConnect={imports.connectSelected}
          onRefresh={imports.refresh}
        />
      </div>
      <div className="coverage-line" aria-live="polite" hidden>
        {snapshotStale && imports.sourceSnapshot?.confirmedAt === null ? (
          <span>
            {t("来源状态尚未核对，无法确认已连接、可连接和活动任务。")}
          </span>
        ) : (
          <>
            {snapshotStale && <span>{t("最近确认：")}</span>}
            <span>{t("{count} 个已连接", { count: connected.length })}</span>
            <span>·</span>
            <span>
              {t("{count} 个可连接", { count: availableUnconnected.length })}
            </span>
            <span>·</span>
            {snapshotStale ? (
              <span>
                {t("{count} 个待核对的活动任务", {
                  count: imports.jobs.filter(isActive).length,
                })}
              </span>
            ) : (
              <>
                {imports.jobs.some(
                  (job) => isActive(job) && !isSyncJob(job),
                ) && (
                  <span>
                    {t("{count} 个正在导入历史", {
                      count: imports.jobs.filter(
                        (job) => isActive(job) && !isSyncJob(job),
                      ).length,
                    })}
                  </span>
                )}
                {imports.jobs.some(
                  (job) => isActive(job) && isSyncJob(job),
                ) && (
                  <span>
                    {t("{count} 个正在检查新增", {
                      count: imports.jobs.filter(
                        (job) => isActive(job) && isSyncJob(job),
                      ).length,
                    })}
                  </span>
                )}
                {!imports.jobs.some(isActive) && (
                  <span>{t("{count} 个活动任务", { count: 0 })}</span>
                )}
              </>
            )}
          </>
        )}
      </div>

      {imports.error && !consentOpen ? (
        <p className="error-banner" role="alert">
          {imports.error}
        </p>
      ) : null}

      {imports.lastConnectResult ? (
        <section
          className="batch-results"
          aria-label={t("批量连接结果")}
          aria-live="polite"
        >
          <strong>{t("本次授权与导入结果")}</strong>
          <ul>
            {imports.lastConnectResult.accepted_source_ids.map((sourceId) => (
              <li className="batch-success" key={sourceId}>
                <span>{sourceName(visibleSources, sourceId)}</span>
                <span>{t("已授权，导入任务已开始")}</span>
              </li>
            ))}
            {Object.entries(imports.lastConnectResult.failed_sources).map(
              ([sourceId, reason]) => (
                <li className="batch-failure" key={sourceId}>
                  <span>{sourceName(visibleSources, sourceId)}</span>
                  <span>
                    {t("未能授权：")}
                    {failureLabel(reason, language)}
                  </span>
                  <button
                    type="button"
                    className="quiet"
                    disabled={
                      actionBusy ||
                      !visibleSources.some(
                        (source) =>
                          source.source_id === sourceId && source.available,
                      )
                    }
                    onClick={() => {
                      const source = visibleSources.find(
                        (item) => item.source_id === sourceId,
                      );
                      if (!source) return;
                      setConsentSources([{ ...source }]);
                      setRetryFailedConnection(true);
                      setConnectionError(null);
                      setConsentPhase("general");
                    }}
                  >
                    <RefreshCw size={14} aria-hidden="true" />
                    {t("重试此来源")}
                  </button>
                </li>
              ),
            )}
          </ul>
        </section>
      ) : null}

      <h2 className="app-section-title">{t("导入来源")}</h2>
      <div className="source-table" aria-busy={imports.loading}>
        {visibleSources
          .slice()
          .sort(sourceOrder)
          .map((source) => {
            const job = imports.latestJobBySource.get(source.source_id);
            return (
              <article className="source-row" key={source.source_id}>
                <label className="source-choice">
                  <input
                    type="checkbox"
                    name={`source-${source.source_id}`}
                    aria-label={t("处理 {name}", { name: source.display_name })}
                    checked={source.authorized && source.sync_enabled === true}
                    aria-busy={pendingProcessingSource === source.source_id}
                    disabled={
                      (!source.available && !source.authorized) ||
                      snapshotStale ||
                      authorizingSource === source.source_id ||
                      pendingProcessingSource === source.source_id ||
                      imports.busyKey !== null
                    }
                    onChange={(event) => {
                      void toggleSourceProcessing(source, event.target.checked);
                    }}
                  />
                  <span>
                    <strong>{source.display_name}</strong>
                    <small>
                      {source.sync_enabled
                        ? t("处理中：历史待办与新会话会继续进入记忆流程")
                        : t("已冻结：待处理 Job 保留，不再扫描新会话")}
                    </small>
                    <small title={source.candidate_path}>
                      {source.candidate_path}
                      {source.candidate_kind === "environment_override"
                        ? t(" · 环境变量覆盖路径")
                        : ""}
                    </small>
                  </span>
                </label>
                <p
                  className={`source-state state-${snapshotStale ? "neutral" : stateTone(source, job)}`}
                >
                  {snapshotStale ? t("待核对") : t(stateLabel(source, job))}
                </p>
                <p className="source-counts">
                  {snapshotStale ? t("最近确认：") : ""}
                  {source.authorized
                    ? t(
                        "累计 {conversations} 个会话 · {messages} 条消息{current} · {capability}",
                        {
                          conversations: source.imported_conversations,
                          messages: source.imported_messages,
                          current: job
                            ? t(" · {current}", {
                                current: t(
                                  isSyncJob(job)
                                    ? "本次同步新增 {count} 条"
                                    : "本次导入新增 {count} 条",
                                  { count: job.stored },
                                ),
                              })
                            : "",
                          capability: t(capabilityLabel(source)),
                        },
                      )
                    : job
                      ? t("{current} · {merged} 条已存在 · {skipped} 条跳过", {
                          current: t(
                            isSyncJob(job)
                              ? "本次同步新增 {count} 条"
                              : "本次导入新增 {count} 条",
                            { count: job.stored },
                          ),
                          merged: job.merged,
                          skipped: job.skipped,
                        })
                      : source.available
                        ? (foundSessionsLabel(source, language) ??
                          t("已发现保存目录"))
                        : t("这台 Mac 上未发现")}
                  {job && job.errors > 0
                    ? t(" · {count} 条异常", { count: job.errors })
                    : ""}
                </p>
                <div className="source-row-actions">
                  {authorizationErrors[source.source_id] ? (
                    <span className="source-authorization-error" role="alert">
                      {authorizationErrors[source.source_id]}
                    </span>
                  ) : null}
                  {source.authorized &&
                    job?.status === "completed" &&
                    job.errors > 0 && (
                      <button
                        type="button"
                        className="secondary"
                        onClick={() =>
                          void imports.startImport(source.source_id)
                        }
                      >
                        {t("重新扫描")}
                      </button>
                    )}
                </div>
                <SourceDirectoryPicker
                  source={source}
                  disabled={
                    connecting ||
                    consentOpen ||
                    installPreview !== null ||
                    uninstallPreview !== null ||
                    hostBusy ||
                    imports.busyKey !== null ||
                    (directoryBusy !== null &&
                      directoryBusy !== source.source_id)
                  }
                  onBusyChange={(active) =>
                    setDirectoryBusy(active ? source.source_id : null)
                  }
                  onSaved={async () => {
                    setConsentSources([]);
                    setConsentPhase(null);
                    await imports.refresh();
                  }}
                />
                {job && job.status !== "completed" && (
                  <SourceJobActions
                    key={job.job_id}
                    job={job}
                    sourceLabel={source.display_name}
                    syncEnabled={source.sync_enabled === true}
                    snapshotStale={snapshotStale}
                    disabled={
                      consentOpen ||
                      installPreview !== null ||
                      uninstallPreview !== null ||
                      hostBusy ||
                      imports.busyKey !== null
                    }
                    onAction={imports.changeJob}
                  />
                )}
                <SourceImportReceipt source={source} />
              </article>
            );
          })}
      </div>

      <section className="host-recall-card" aria-live="polite">
        <div>
          <h2>{t("使用记忆")}</h2>
          <p>{t("各应用的读取连接与导入授权分开管理。")}</p>
          {bulkTargets.length > 0 && (
            <button
              type="button"
              className="secondary"
              disabled={
                actionBusy ||
                hostBusy ||
                consentOpen ||
                installPreview !== null ||
                uninstallPreview !== null
              }
              onClick={() => void prepareBulk()}
            >
              <ShieldCheck size={14} aria-hidden="true" />
              {bulkBusy && bulk === null
                ? t("正在准备…")
                : t("一键接入全部 {count} 个应用", {
                    count: bulkTargets.length,
                  })}
            </button>
          )}
        </div>
        {recallHosts === "unavailable" ? (
          <small>{t("请在 MemHub 桌面 App 中安装。")}</small>
        ) : recallHosts === "checking" ? (
          <small>{t("正在检查…")}</small>
        ) : recallHosts === "error" ? (
          <small>{t("无法读取安装状态。")}</small>
        ) : recallHosts.length === 0 ? (
          <small>{t("本机没有检测到支持召回的 App。")}</small>
        ) : (
          <ul className="recall-host-list">
            {recallHosts.map((host) => (
              <li key={host.hostId}>
                <span>{host.label}</span>
                {/* The link's own verdict, kept apart from "installed".
                    Installed only means our files are intact, which was true
                    of the three hosts that pointed at a wrong discovery path
                    and recalled nothing. Worded so that green is the only
                    thing that reads as working. */}
                {needsRecallReconnect(host) ? (
                  <span className="recall-link recall-link-unreachable">
                    {t("链路：需要重新连接")}
                  </span>
                ) : host.installed && host.selfCheck ? (
                  <span
                    className={`recall-link recall-link-${host.selfCheck.state}`}
                  >
                    {t("链路：")}
                    {LINK_WORDING[host.selfCheck.state]
                      ? t(LINK_WORDING[host.selfCheck.state])
                      : t("状态未知")}
                  </span>
                ) : null}
                {host.installed && host.skillDrift ? (
                  <span
                    className={`recall-skill recall-skill-${host.skillDrift.state}`}
                  >
                    {t("调用规则：")}
                    {SKILL_WORDING[host.skillDrift.state]
                      ? t(SKILL_WORDING[host.skillDrift.state])
                      : t("状态未知")}
                  </span>
                ) : null}
                {host.installed &&
                !needsRecallReconnect(host) &&
                host.skillDrift &&
                host.skillDrift.state !== "in_sync" ? (
                  <button
                    type="button"
                    disabled={busyHost === host.hostId || bulkBusy}
                    onClick={async () => {
                      if (!hostCapabilities().skill) return;
                      setBusyHost(host.hostId);
                      try {
                        const preview = await previewHostSkill(host.hostId);
                        if (preview.upToDate) return;
                        if (preview.conflict) {
                          setSkillConflict({
                            hostId: host.hostId,
                            fingerprint: preview.fingerprint,
                          });
                          return;
                        }
                        setInstallPreview(preview);
                      } catch {
                        markHostsUnreadable();
                      } finally {
                        setBusyHost(null);
                      }
                    }}
                  >
                    {t("更新调用规则")}
                  </button>
                ) : null}
                {skillConflict?.hostId === host.hostId ? (
                  <span className="recall-skill-conflict">
                    {t("你手动修改过这份调用规则。更新会覆盖你的修改。")}
                    <button
                      type="button"
                      disabled={busyHost === host.hostId || bulkBusy}
                      onClick={async () => {
                        if (!hostCapabilities().skill || !skillConflict) return;
                        setBusyHost(host.hostId);
                        try {
                          await applyHostSkill({
                            hostId: host.hostId,
                            fingerprint: skillConflict.fingerprint,
                            overwrite: true,
                          });
                          setSkillConflict(null);
                          setHostActionFailed(false);
                        } catch {
                          markHostsUnreadable();
                        } finally {
                          setBusyHost(null);
                        }
                      }}
                    >
                      {t("仍要覆盖")}
                    </button>
                  </span>
                ) : null}
                {host.installed &&
                !needsRecallReconnect(host) &&
                host.selfCheck &&
                host.selfCheck.state !== "wired" ? (
                  <button
                    type="button"
                    disabled={busyHost === host.hostId || bulkBusy}
                    onClick={async () => {
                      if (!window.oneMemory) return;
                      setBusyHost(host.hostId);
                      try {
                        const check = await probeHost(host.hostId);
                        setLinkCheck((checks) => ({
                          ...checks,
                          [host.hostId]: check,
                        }));
                      } catch {
                        markHostsUnreadable();
                      } finally {
                        setBusyHost(null);
                      }
                    }}
                  >
                    {t("诊断")}
                  </button>
                ) : null}
                {linkCheck[host.hostId]?.repoint ? (
                  <button
                    type="button"
                    disabled={busyHost === host.hostId || bulkBusy}
                    onClick={async () => {
                      if (!window.oneMemory) return;
                      setBusyHost(host.hostId);
                      try {
                        // The same preview install goes through. Repointing
                        // is an install with the right path, so it is shown
                        // and approved like any other edit to their file
                        // rather than written behind their back.
                        setInstallPreview(
                          await previewHostInstall(host.hostId),
                        );
                      } catch (error) {
                        if (requiresHookRemoval(error))
                          setHookRemovalRequired(host.hostId);
                        else markHostsUnreadable();
                      } finally {
                        setBusyHost(null);
                      }
                    }}
                  >
                    {t("重新指向")}
                  </button>
                ) : null}
                {linkCheck[host.hostId] !== undefined &&
                linkCheck[host.hostId]?.repoint === null &&
                linkCheck[host.hostId]?.probe.state !== "wired" ? (
                  <span className="recall-link-detail">
                    {linkCheck[host.hostId]?.probe.detail}
                  </span>
                ) : null}
                <button
                  type="button"
                  disabled={busyHost === host.hostId || bulkBusy}
                  onClick={async () => {
                    if (!window.oneMemory) return;
                    setBusyHost(host.hostId);
                    setHookRemovalRequired(null);
                    setHostErrors((errors) => ({
                      ...errors,
                      [host.hostId]: "",
                    }));
                    try {
                      // Both directions edit this other program's own settings
                      // file, so both are shown first and applied only on
                      // approval. Disconnecting restoring what was there is
                      // still an edit to a file that is not ours, and the user
                      // may have added a server of their own since.
                      if (!host.installed || needsRecallReconnect(host)) {
                        setInstallPreview(
                          await previewHostInstall(host.hostId),
                        );
                        return;
                      }
                      setUninstallPreview(
                        await previewHostUninstall(host.hostId),
                      );
                    } catch (error) {
                      if (requiresHookRemoval(error))
                        setHookRemovalRequired(host.hostId);
                      else
                        setHostErrors((errors) => ({
                          ...errors,
                          [host.hostId]:
                            "未能读取连接变更预览。可以重试；尚未请求写入。",
                        }));
                    } finally {
                      setBusyHost(null);
                    }
                  }}
                >
                  {busyHost === host.hostId
                    ? t("连接中…")
                    : needsRecallReconnect(host)
                      ? t("一键重新连接")
                      : host.installed
                        ? t("已安装 · 点击断开")
                        : t("一键安装到 {name}", { name: host.label })}
                </button>
                {hostErrors[host.hostId] && (
                  <p className="recall-link-detail" role="alert">
                    {t("未能读取连接变更预览。可以重试；尚未请求写入。")}
                  </p>
                )}
                <RecallPermissionControl
                  host={host}
                  sources={imports.sources}
                  disabled={
                    hostBusy ||
                    consentOpen ||
                    installPreview !== null ||
                    uninstallPreview !== null
                  }
                  onChanged={async () => {
                    if (!window.oneMemory)
                      throw new Error("桌面连接暂不可用，请稍后核对。");
                    await rereadHosts();
                  }}
                />
                <SyncHookControl
                  host={host}
                  disabled={
                    hostBusy ||
                    consentOpen ||
                    installPreview !== null ||
                    uninstallPreview !== null
                  }
                />
                {hookRemovalRequired === host.hostId && (
                  <p role="alert" className="sync-hook-error">
                    {t("请先移除同步 Hook，再断开应用连接。")}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
      <ConnectionFallback />
      <details className="application-coverage">
        <summary>{t("来源支持范围")}</summary>
        <table>
          <thead>
            <tr>
              <th>{t("应用 / 数据")}</th>
              <th>{t("历史导入")}</th>
              <th>{t("新增同步")}</th>
              <th>{t("外部读取")}</th>
            </tr>
          </thead>
          <tbody>
            {imports.sources.map((source) => {
              const host = Array.isArray(recallHosts)
                ? recallHosts.find((item) => item.hostId === source.source_id)
                : undefined;
              const file = source.source_id.endsWith("-export");
              return (
                <tr key={source.source_id}>
                  <th>{source.display_name}</th>
                  <td>
                    {file
                      ? t("文件导入")
                      : source.available
                        ? t("本机已发现")
                        : t("本机未发现")}
                  </td>
                  <td>
                    {file
                      ? t("一次性快照")
                      : source.capabilities.live_sync === "implemented"
                        ? t("文件监听 / 轮询")
                        : t("待接入")}
                  </td>
                  <td>
                    {file
                      ? t("无桌面 Hook")
                      : host?.installed
                        ? t("已安装，需实际验证")
                        : host?.present
                          ? t("可安装")
                          : t("未验证")}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </details>

      <p className="privacy-note">
        {t(
          "不读取屏幕、系统辅助功能、键盘、音频或剪贴板。每个来源的授权可单独撤销。",
        )}
      </p>

      {installPreview ? (
        <InstallPreviewSheet
          preview={installPreview}
          busy={busyHost === installPreview.hostId}
          onCancel={() => setInstallPreview(null)}
          onApprove={async () => {
            if (!window.oneMemory) return;
            const { hostId, fingerprint } = installPreview;
            setBusyHost(hostId);
            try {
              await installHost({ hostId, fingerprint });
              setInstallPreview(null);
              // 需求 8：一次点击完成连接。安装落地后直接按默认全选授予调用权限，
              // 不再要求用户回到应用行再点一次「允许使用记忆」。
              let granted = true;
              try {
                await allowHost(hostId);
              } catch {
                // 连接已经建立，授权失败只留到行内提示，不影响这次连接结果。
                // 失败的授权已在状态层重读过一次，这里不再重读。
                granted = false;
              }
              if (granted)
                try {
                  await rereadHosts();
                } catch {
                  // 列表刷新失败不撤销已完成的连接与授权。
                }
            } catch (error) {
              if (requiresHookRemoval(error)) setHookRemovalRequired(hostId);
              else markHostsUnreadable();
              setInstallPreview(null);
            } finally {
              setBusyHost(null);
            }
          }}
        />
      ) : null}

      {bulk ? (
        <BulkConnectSheet
          steps={bulk}
          busy={bulkBusy}
          errors={bulkErrors}
          done={bulkDone}
          onCancel={() => setBulk(null)}
          onApprove={() => void runBulk()}
        />
      ) : null}

      {uninstallPreview ? (
        <UninstallPreviewSheet
          preview={uninstallPreview}
          busy={busyHost === uninstallPreview.hostId}
          onCancel={() => setUninstallPreview(null)}
          onApprove={async () => {
            if (!window.oneMemory) return;
            const { hostId, fingerprint } = uninstallPreview;
            setBusyHost(hostId);
            try {
              await uninstallHost({ hostId, fingerprint });
              setUninstallPreview(null);
            } catch (error) {
              if (requiresHookRemoval(error)) setHookRemovalRequired(hostId);
              else markHostsUnreadable();
              setUninstallPreview(null);
            } finally {
              setBusyHost(null);
            }
          }}
        />
      ) : null}

      {consentPhase ? (
        <div className="consent-backdrop">
          <section
            ref={consentPanel}
            className="consent-sheet"
            role="dialog"
            aria-modal="true"
            aria-busy={connecting}
            tabIndex={-1}
            aria-labelledby={
              consentPhase === "general"
                ? "consent-title"
                : "override-consent-title"
            }
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.preventDefault();
                event.stopPropagation();
                if (!connecting) {
                  setConsentPhase(null);
                  consentTrace.current?.done("rejected", {
                    reason: "consent_dismissed",
                  });
                  consentTrace.current = null;
                }
              }
              if (event.key === "Tab") {
                const buttons =
                  consentPanel.current?.querySelectorAll<HTMLButtonElement>(
                    "button:not(:disabled)",
                  );
                const first = buttons?.[0],
                  last = buttons?.[buttons.length - 1];
                if (!first) {
                  event.preventDefault();
                  consentPanel.current?.focus();
                } else if (
                  event.shiftKey &&
                  (document.activeElement === first ||
                    document.activeElement === consentPanel.current)
                ) {
                  event.preventDefault();
                  last?.focus();
                } else if (!event.shiftKey && document.activeElement === last) {
                  event.preventDefault();
                  first.focus();
                }
              }
            }}
          >
            {consentPhase === "general" ? (
              <>
                <p className="eyebrow">{t("一次确认")}</p>
                <h2 id="consent-title">{t("允许读取所选来源的文本对话？")}</h2>
                <p>
                  {t(
                    "MemHub 将读取下面来源中已经保存的文本对话，并保留在本机。模型处理、持续同步和外部应用读取需要另外授权。",
                  )}
                </p>
                <ConsentSourceList sources={consentSources} />
                <p className="consent-exclusion">
                  {t("不读取屏幕、键盘、音频、剪贴板或其他应用实时内容。")}
                </p>
                {connectionError && (
                  <p className="error-banner" role="alert">
                    {consentConnectionError}
                  </p>
                )}
                <div className="consent-actions">
                  <button
                    type="button"
                    className="secondary"
                    disabled={connecting}
                    onClick={() => setConsentPhase(null)}
                  >
                    {t("取消")}
                  </button>
                  <button
                    type="button"
                    disabled={connecting}
                    onClick={() => {
                      if (overrideSources.length) {
                        setConsentPhase("environment_override");
                        return;
                      }
                      void connectApproved();
                    }}
                  >
                    {connecting
                      ? t("正在连接…")
                      : connectionError
                        ? t("重试连接")
                        : t("允许并开始导入")}
                  </button>
                </div>
              </>
            ) : (
              <>
                <p className="eyebrow">{t("二次确认 · 环境变量覆盖")}</p>
                <h2 id="override-consent-title">
                  {t("确认读取这些自定义路径？")}
                </h2>
                <p>
                  {t(
                    "以下目录由环境变量指定，不是应用的默认保存位置。请确认路径确实属于对应的 AI 应用。",
                  )}
                </p>
                <ConsentSourceList sources={overrideSources} />
                {connectionError && (
                  <p className="error-banner" role="alert">
                    {consentConnectionError}
                  </p>
                )}
                <div className="consent-actions">
                  <button
                    type="button"
                    className="secondary"
                    disabled={connecting}
                    onClick={() => setConsentPhase("general")}
                  >
                    {t("返回")}
                  </button>
                  <button
                    type="button"
                    disabled={connecting}
                    onClick={() => void connectApproved()}
                  >
                    {connecting
                      ? t("正在连接…")
                      : connectionError
                        ? t("重试连接")
                        : t("确认路径并授权导入")}
                  </button>
                </div>
              </>
            )}
          </section>
        </div>
      ) : null}
    </section>
  );
}

function ConsentSourceList({ sources }: { sources: ImportState["sources"] }) {
  return (
    <ul className="consent-source-list">
      {sources.map((source) => (
        <li key={source.source_id}>
          <strong>{source.display_name}</strong>
          <span>{source.candidate_path}</span>
        </li>
      ))}
    </ul>
  );
}

function sourceName(sources: ImportState["sources"], sourceId: string): string {
  return (
    sources.find((source) => source.source_id === sourceId)?.display_name ??
    sourceId
  );
}

function failureLabel(
  reason: string,
  language: ReturnType<typeof useUiLanguage>,
): string {
  const labels: Record<string, UiMessage> = {
    grant_not_available: "授权记录未建立",
    environment_override_confirmation_required: "自定义路径尚未确认",
    source_not_available: "数据目录当前不可用",
  };
  return labels[reason] ? translate(language, labels[reason]) : reason;
}

function isActive(job: { status: string }) {
  return ["pending", "running"].includes(job.status);
}

function isSyncJob(job: ImportState["jobs"][number]) {
  return job.operation === "sync";
}

function foundSessionsLabel(
  source: ImportState["sources"][number],
  language: ReturnType<typeof useUiLanguage>,
): string | null {
  // Only print a number the Core actually measured. An absent or null
  // session_count means "not measured", not zero, so it must never turn into a
  // number on screen. An installed host with nothing readable yet still owes
  // the person an honest sentence rather than a bare zero, which reads as a
  // broken scan.
  const count = source.session_count;
  if (count === null || count === undefined) return null;
  return count > 0
    ? translate(language, "找到 {count} 个会话", { count })
    : translate(language, "已安装，暂未发现会话");
}

function capabilityLabel(source: ImportState["sources"][number]): UiMessage {
  if (source.sync_completeness === "one_time_snapshot")
    return "一次性快照，不跟随原应用变化";
  // Deliberately no interval here: the poll period is a server setting
  // (live_sync_interval_seconds, default 60s) that the browser never sees, and
  // the number previously written here said 5 minutes — five times the truth.
  const following =
    source.sync_completeness === "full_dynamic" ? "完整动态同步" : "仅新增同步";
  return source.capabilities.live_sync === "implemented"
    ? following
    : "仅手动导入";
}

function sourceOrder(
  left: ImportState["sources"][number],
  right: ImportState["sources"][number],
) {
  return (
    Number(right.authorized) - Number(left.authorized) ||
    Number(right.available) - Number(left.available) ||
    left.display_name.localeCompare(right.display_name)
  );
}

function stateLabel(
  source: ImportState["sources"][number],
  job: ImportState["jobs"][number] | undefined,
): UiMessage {
  if (job && isSyncJob(job)) {
    const labels: Record<ImportState["jobs"][number]["status"], UiMessage> = {
      pending: "等待检查新增",
      running: "正在检查新增",
      paused: "新增同步已暂停",
      interrupted: "新增同步已中断",
      failed: "新增同步失败",
      completed: job.errors > 0 ? "新增同步有异常" : "新增检查已完成",
      cancelled: "新增同步已取消",
    };
    return labels[job.status];
  }
  if (job?.status === "pending") return "等待导入";
  if (job?.status === "running") return "正在导入";
  if (job?.status === "completed" && job.errors > 0) return "导入有异常";
  if (job?.status === "failed") return "需要处理";
  if (job?.status === "paused" || job?.status === "interrupted")
    return "进度已保留";
  if (job?.status === "cancelled") return "导入已取消";
  if (
    source.authorized &&
    job?.status === "completed" &&
    source.imported_messages === 0 &&
    job.stored === 0 &&
    job.skipped
  )
    return "未识别到可导入对话";
  if (source.authorized) return "已连接";
  if (source.available) return "可连接";
  return "未发现来源位置";
}

function stateTone(
  source: ImportState["sources"][number],
  job: ImportState["jobs"][number] | undefined,
) {
  if (
    job?.status === "failed" ||
    (job?.status === "completed" && job.errors > 0) ||
    (job?.status === "completed" &&
      !isSyncJob(job) &&
      source.imported_messages === 0 &&
      job.stored === 0 &&
      job.skipped)
  )
    return "warning";
  if (job && isActive(job)) return "active";
  if (source.authorized) return "success";
  return "neutral";
}

/** The edit an install would make, shown before it is made.
 *
 * Recall is installed by writing into another program's own settings file. The
 * user is the one who owns that file, so they see the lines that would change
 * — not a summary of them — and approve that specific edit.
 */
function InstallPreviewSheet({
  preview,
  busy,
  onCancel,
  onApprove,
}: {
  preview: RecallInstallPreview;
  busy: boolean;
  onCancel: () => void;
  onApprove: () => void;
}) {
  const t = useTranslate();
  return (
    <div className="consent-backdrop">
      <section
        className="consent-sheet install-preview-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby="install-preview-title"
        onKeyDown={(event) => {
          if (event.key === "Escape" && !busy) onCancel();
        }}
      >
        <p className="eyebrow">{t("安装前确认")}</p>
        <h2 id="install-preview-title">
          {t("将这样修改 {name} 的配置", { name: preview.hostLabel })}
        </h2>
        <p>
          {t(
            "安装召回需要写入 {name} 自己的配置文件。下面是会改动的行，确认后才会写入；随时可以断开并还原。",
            { name: preview.hostLabel },
          )}
        </p>
        <p className="consent-exclusion">
          {t(
            "确认后会一并按默认范围允许 {name} 使用记忆（全部工具与已导入来源），不用再单独点一次授权；想收窄范围，连接完成后在应用行内点「调整范围」即可。",
            { name: preview.hostLabel },
          )}
        </p>
        <InstallFileList preview={preview} hostLabel={preview.hostLabel} />
        <div className="consent-actions">
          <button
            type="button"
            className="secondary"
            disabled={busy}
            onClick={onCancel}
          >
            {t("取消")}
          </button>
          <button type="button" disabled={busy} onClick={onApprove}>
            {busy ? t("连接中…") : t("连接并允许使用记忆")}
          </button>
        </div>
      </section>
    </div>
  );
}

/** The lines one install would change, for one host.
 *
 * Shared by the single-host sheet and the bulk sheet: the same edit has to be
 * readable in both, because approving several installs at once is only the
 * same decision repeated, not a different one.
 */
function InstallFileList({
  preview,
  hostLabel,
}: {
  preview: RecallInstallPreview;
  hostLabel: string;
}) {
  const t = useTranslate();
  const changed = preview.files.filter((file) => file.hunk !== null);
  if (!changed.length)
    return <p className="consent-exclusion">{t("没有需要改动的内容。")}</p>;
  return (
    <ul className="install-preview-files">
      {changed.map((file) => (
        <li key={file.path}>
          <div className="install-preview-file-head">
            <code>{file.path}</code>
            <span
              className={
                file.owner === "host"
                  ? "badge badge-warning"
                  : "badge badge-neutral"
              }
            >
              {file.owner === "host"
                ? file.existed
                  ? t("{name} 的现有文件 · 合并写入", { name: hostLabel })
                  : t("{name} 的配置 · 新建", { name: hostLabel })
                : file.existed
                  ? t("MemHub 的文件 · 替换")
                  : t("MemHub 的文件 · 新建")}
            </span>
          </div>
          {/* Lines wrap rather than scroll sideways: the path above already
              names this block, and a scroll region no keyboard can reach would
              hide the end of a long line. */}
          <pre className="install-preview-diff">
            <DiffLines tone="context" lines={file.hunk?.contextBefore} />
            <DiffLines tone="removed" lines={file.hunk?.removed} />
            <DiffLines tone="added" lines={file.hunk?.added} />
            <DiffLines tone="context" lines={file.hunk?.contextAfter} />
          </pre>
        </li>
      ))}
    </ul>
  );
}

/** Every app at once, in one confirmation.
 *
 * Connecting is per-app work: each one means a write into that program's own
 * settings file plus a grant, and doing it app by app meant the same two
 * approvals over and over. This shows what each install would write — the same
 * lines as the single-app sheet — and one button that does all of it.
 */
function bulkReasonLabel(
  reason: string,
  language: ReturnType<typeof useUiLanguage>,
): string {
  if (reason === "请先移除这个应用的同步 Hook")
    return translate(language, "请先移除这个应用的同步 Hook");
  if (reason === "未能读取这个应用的连接变更预览")
    return translate(language, "未能读取这个应用的连接变更预览");
  if (reason === "未能完成") return translate(language, "未能完成");
  return hostReasonLabel(reason, language);
}

function BulkConnectSheet({
  steps,
  busy,
  errors,
  done,
  onCancel,
  onApprove,
}: {
  steps: BulkStep[];
  busy: boolean;
  errors: Record<string, string>;
  done: string[] | null;
  onCancel: () => void;
  onApprove: () => void;
}) {
  const t = useTranslate();
  const language = useUiLanguage();
  const pending = steps.filter((step) => !step.error);
  const skipped = steps.length - pending.length;
  const unfinished = steps.flatMap((step) => {
    const reason = step.error ?? errors[step.hostId];
    return reason
      ? [
          t("{name}（{reason}）", {
            name: step.label,
            reason: bulkReasonLabel(reason, language),
          }),
        ]
      : [];
  });
  return (
    <div className="consent-backdrop">
      <section
        className="consent-sheet install-preview-sheet"
        role="dialog"
        aria-modal="true"
        aria-busy={busy}
        aria-labelledby="bulk-connect-title"
        onKeyDown={(event) => {
          if (event.key === "Escape" && !busy) onCancel();
        }}
      >
        <p className="eyebrow">{t("一次确认")}</p>
        <h2 id="bulk-connect-title">
          {t("接入 {count} 个应用", { count: pending.length })}
        </h2>
        <p>
          {t(
            "每个应用会装上读取连接，并按当前全部可核对的工具与范围允许使用记忆。下面是会写入的内容，确认后才会写入；之后可以逐个断开。",
          )}
        </p>
        <ul className="bulk-host-list">
          {steps.map((step) => {
            const changed =
              step.install?.files.filter((file) => file.hunk !== null).length ??
              0;
            return (
              <li key={step.hostId}>
                <div className="bulk-host-head">
                  <strong>{step.label}</strong>
                  <span>
                    {step.error
                      ? t("跳过：{reason}", {
                          reason: bulkReasonLabel(step.error, language),
                        })
                      : step.install
                        ? t("安装连接，并允许使用记忆")
                        : t("已安装，允许使用记忆")}
                  </span>
                </div>
                {step.install ? (
                  <details>
                    <summary>
                      {t("将改动 {count} 个文件", { count: changed })}
                    </summary>
                    <InstallFileList
                      preview={step.install}
                      hostLabel={step.label}
                    />
                  </details>
                ) : null}
                {errors[step.hostId] && (
                  <p className="recall-link-detail" role="alert">
                    {t("{reason}。可单独重试或稍后再试。", {
                      reason: bulkReasonLabel(errors[step.hostId], language),
                    })}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
        {skipped > 0 && (
          <p className="consent-exclusion">
            {t("有 {count} 个应用无法准备，已跳过，其他应用不受影响。", {
              count: skipped,
            })}
          </p>
        )}
        {done && (
          <p role="status">
            {unfinished.length
              ? t(
                  "{count} 个应用已接入，{unfinished} 个未完成：{names}。可在下方逐个重试。",
                  {
                    count: done.length,
                    unfinished: unfinished.length,
                    names: unfinished.join(language === "en" ? ", " : "、"),
                  },
                )
              : t("{count} 个应用已接入。", { count: done.length })}
          </p>
        )}
        <div className="consent-actions">
          <button
            type="button"
            className="secondary"
            disabled={busy}
            onClick={onCancel}
          >
            {done ? t("关闭") : t("取消")}
          </button>
          {done ? null : (
            <button
              type="button"
              disabled={busy || pending.length === 0}
              onClick={onApprove}
            >
              {busy
                ? t("接入中…")
                : t("确认接入 {count} 个应用", { count: pending.length })}
            </button>
          )}
        </div>
      </section>
    </div>
  );
}

/** The same review, for the edit that takes us back out.
 *
 * Disconnecting was the one operation that rewrote another program's
 * configuration with nothing shown first. It is a smaller edit than installing
 * but not a safer one: the host's file has been live since, and what comes back
 * is whatever was recorded at install time.
 */
function UninstallPreviewSheet({
  preview,
  busy,
  onCancel,
  onApprove,
}: {
  preview: RecallUninstallPreview;
  busy: boolean;
  onCancel: () => void;
  onApprove: () => void;
}) {
  const t = useTranslate();
  const changed = preview.files.filter(
    (file) => file.hunk !== null || file.deleted,
  );
  return (
    <div className="consent-backdrop">
      <section
        className="consent-sheet install-preview-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby="uninstall-preview-title"
        onKeyDown={(event) => {
          if (event.key === "Escape" && !busy) onCancel();
        }}
      >
        <p className="eyebrow">{t("断开前确认")}</p>
        <h2 id="uninstall-preview-title">
          {t("将这样还原 {name} 的配置", { name: preview.hostLabel })}
        </h2>
        <p>
          {t(
            "断开会撤销安装时写入的内容，恢复安装前的样子。下面是会改动的行，确认后才会写入；之后可以重新安装。",
          )}
        </p>
        {changed.length === 0 ? (
          <p className="consent-exclusion">{t("没有需要改动的内容。")}</p>
        ) : (
          <ul className="install-preview-files">
            {changed.map((file) => (
              <li key={file.path}>
                <div className="install-preview-file-head">
                  <code>{file.path}</code>
                  <span
                    className={
                      file.owner === "host"
                        ? "badge badge-warning"
                        : "badge badge-neutral"
                    }
                  >
                    {file.deleted
                      ? file.owner === "host"
                        ? t("{name} 的配置 · 删除（安装前不存在）", {
                            name: preview.hostLabel,
                          })
                        : t("MemHub 的文件 · 删除")
                      : file.owner === "host"
                        ? t("{name} 的现有文件 · 还原", {
                            name: preview.hostLabel,
                          })
                        : t("MemHub 的文件 · 还原")}
                  </span>
                </div>
                {file.deleted ? (
                  <p className="consent-exclusion">
                    {t("整个文件会被移除，不留空目录。")}
                  </p>
                ) : (
                  <pre className="install-preview-diff">
                    <DiffLines
                      tone="context"
                      lines={file.hunk?.contextBefore}
                    />
                    <DiffLines tone="removed" lines={file.hunk?.removed} />
                    <DiffLines tone="added" lines={file.hunk?.added} />
                    <DiffLines tone="context" lines={file.hunk?.contextAfter} />
                  </pre>
                )}
              </li>
            ))}
          </ul>
        )}
        <div className="consent-actions">
          <button
            type="button"
            className="secondary"
            disabled={busy}
            onClick={onCancel}
          >
            {t("取消")}
          </button>
          <button type="button" disabled={busy} onClick={onApprove}>
            {busy ? t("还原中…") : t("确认断开")}
          </button>
        </div>
      </section>
    </div>
  );
}

/** One run of diff lines sharing a tone.
 *
 * Consecutive lines of the same kind are one element rather than one per line:
 * a run of added lines then reads as a single band, and there is no list to
 * key — configuration lines repeat, so their own text cannot identify them and
 * their position is the only thing left, which is not an identity at all.
 */
function DiffLines({
  tone,
  lines,
}: {
  tone: "context" | "removed" | "added";
  lines: string[] | undefined;
}) {
  if (!lines?.length) return null;
  const sign = tone === "removed" ? "-" : tone === "added" ? "+" : " ";
  return (
    <span className={`diff-${tone}`}>
      {lines.map((line) => `${sign} ${line}`).join("\n")}
    </span>
  );
}
