import {
  type SourceResponse,
  translate,
  type UiLanguage,
  type UiMessage,
} from "@one-memory/contracts";
import { useCallback, useEffect, useRef, useState } from "react";
import { AppMark } from "./app-mark";
import { connectionHostId, preferenceHasSource } from "./connection-hosts";
import {
  readConnectionPreferences,
  writeConnectionPreference,
} from "./connection-preferences";
import { noteUserHostChoice } from "./connection-reconcile";
import type { RecallLinkCheck, RecallRemovalFinding } from "./global";
import {
  disconnectHost,
  enableHostConnection,
  hostCapabilities,
  hostConnectionApproved,
  hostConnectionUnsupported,
  hostFailureNote,
  hostRereadFailed,
  hostWriteReason,
  matchHost,
  probeHost,
  refreshRecallHosts,
  setHostMemoryPermission,
  UNSUPPORTED_NOTE,
  useRecallHosts,
} from "./host-state";
import { R6Button, R6Switch } from "./R6Controls";
import {
  latestSource,
  sourceAuthorizationNote,
  useSource,
} from "./source-state";
import { useTranslate, useUiLanguage } from "./ui-language";
import "./application-detail.css";

const SYNC_UNCONFIRMED = "此应用的处理开关结果未能确认，其他应用不受影响。";
// D97: the single sync switch now governs both taking in new data and
// generating memories, so the separate read-permission switch is hidden, not
// removed — flip this back to restore it without digging through history.
const MEMORY_PERMISSION_SECTION_VISIBLE = false;

type InstallNotice =
  | {
      kind: "review";
      name: string;
      revoked: boolean;
      findings: RecallRemovalFinding[];
    }
  | { kind: "revoked"; name: string; configEdited: boolean }
  | {
      kind: "connect" | "permission";
      reason: string | null;
      rereadFailed: boolean;
    }
  | {
      kind: "disconnect";
      name: string;
      reason: string | null;
      rereadFailed: boolean;
    };

function installNoticeText(
  notice: InstallNotice,
  language: UiLanguage,
): string {
  const t = (key: UiMessage, values?: Record<string, string>) =>
    translate(language, key, values);
  if (notice.kind === "review") {
    const locations = notice.findings
      .map((finding) =>
        language === "en"
          ? `${finding.path} (${finding.where})`
          : `${finding.path}（${finding.where}）`,
      )
      .join(language === "en" ? "; " : "；");
    return `${notice.revoked ? t("已撤销记忆权限，{name} 已查不到记忆。但", { name: notice.name }) : ""}${t("无法确认连接已全部移除，需要你手动核对：{findings}。", { findings: locations })}`;
  }
  if (notice.kind === "revoked")
    return `${t("已撤销记忆权限，{name} 已查不到记忆。", { name: notice.name })}${t(
      notice.configEdited
        ? "配置文件被改过，MemHub 没有自动删除；请手动移除其中的 MemHub 连接。"
        : "连接文件没有移除，请重新核对。",
    )}`;
  const message =
    notice.kind === "connect"
      ? t("此应用的一键接入未完成，其他应用不受影响。")
      : notice.kind === "disconnect"
        ? t("连接未能移除，记忆权限也未能撤销，{name} 仍可使用记忆，请重试。", {
            name: notice.name,
          })
        : t("此应用的记忆使用权限未能确认，请重试。");
  return `${message}${hostFailureNote(notice.reason, notice.rereadFailed, language)}`;
}

function connectionLabel(input: {
  approved: boolean;
  insertionOn: boolean;
  pending: boolean;
  failed: boolean;
  probeState: string | null;
}): UiMessage {
  if (input.probeState === "wired") return "已接通";
  if (input.probeState === "reachable_but_blind") return "已安装，但取不到记忆";
  if (input.probeState === "unreachable") return "未接通";
  if (input.failed) return "状态待核对";
  if (input.pending) return "检测中";
  if (input.approved || input.insertionOn) return "未接通";
  return "未开启关联";
}

function SourceIcon({ source }: { source: SourceResponse }) {
  return (
    <AppMark
      id={source.source_id}
      label={source.display_name}
      size={46}
      fallbackClassName="r6-app-detail-initial"
    />
  );
}

export function ApplicationDetail({
  source: given,
  stale,
  syncWriteConfirmed = false,
  setSync,
  refresh,
  onBack,
}: {
  source: SourceResponse;
  sources: SourceResponse[];
  stale: boolean;
  syncWriteConfirmed?: boolean;
  setSync(source: SourceResponse, enabled: boolean): Promise<void>;
  refresh(): Promise<{ sources: SourceResponse[] }>;
  onBack(): void;
}) {
  const language = useUiLanguage();
  const t = useTranslate();
  const source = useSource(given);
  const hosts = useRecallHosts().data;
  const [read, setRead] = useState<{
    sourceId: string;
    state: "loading" | "ready" | "error" | "no-bridge";
  }>({ sourceId: source.source_id, state: "loading" });
  const [checkedAt, setCheckedAt] = useState<string | null>(null);
  const [link, setLink] = useState<RecallLinkCheck | null>(null);
  const [linkFailed, setLinkFailed] = useState(false);
  const [linkPending, setLinkPending] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [installError, setInstallError] = useState<InstallNotice | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState("");
  const [memoryPending, setMemoryPending] = useState(false);
  const [hostRestartNotice, setHostRestartNotice] = useState<UiMessage | "">(
    "",
  );
  const [syncIntent, setSyncIntent] = useState<boolean | null>(null);
  // D97: shown after the switch is turned back on, so the person knows the
  // conversations missed while frozen will be filled in by a backfill import.
  const [backfillNotice, setBackfillNotice] = useState(false);
  const [appLocationNote, setAppLocationNote] = useState<UiMessage | null>(
    null,
  );
  const syncChecked = syncIntent ?? source.sync_enabled === true;
  // The sync switch is the source authorization control, so the only note
  // beside it is about the source. Plugin trouble belongs to the other switch.
  const authorizationNote = sourceAuthorizationNote(source, language);
  const [insertionIntent, setInsertionIntent] = useState<boolean | null>(null);
  const [memoryIntent, setMemoryIntent] = useState<boolean | null>(null);
  const refreshFlight = useRef<object | null>(null);
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;
  const epoch = useRef(0);
  const mounted = useRef(true);
  useEffect(() => {
    const api = window.oneMemory;
    if (!api?.getConnectionSetupCapability) return;
    let active = true;
    void api.getConnectionSetupCapability().then(
      (capability) => {
        if (active && capability.reason === "app_not_in_applications")
          setAppLocationNote(
            "请先把 MemHub 拖到“应用程序”并从那里重新打开，才能接入应用。",
          );
      },
      () => {},
    );
    return () => {
      active = false;
    };
  }, []);
  const insertionBusy = useRef(false);
  const insertionWanted = useRef<boolean | null>(null);
  const memoryBusy = useRef(false);
  const memoryWanted = useRef<boolean | null>(null);
  const syncBusy = useRef(false);
  const syncWanted = useRef<boolean | null>(null);
  const syncApplyStarted = useRef(false);
  // D77: a click that lands while the remembered-sync write is in flight used
  // to be queued and never sent, leaving the switch and header disagreeing.
  const drainSyncRef = useRef<() => Promise<void>>(async () => {});
  const setSyncRef = useRef(setSync);
  setSyncRef.current = setSync;
  const probeLink = useCallback(async (hostId: string) => {
    if (!hostCapabilities().probe) return;
    setLinkPending(true);
    setLinkFailed(false);
    try {
      const result = await probeHost(hostId);
      if (mounted.current) {
        setLink(result);
        setLinkFailed(false);
      }
    } catch {
      if (mounted.current) {
        setLink(null);
        setLinkFailed(true);
      }
    } finally {
      if (mounted.current) setLinkPending(false);
    }
  }, []);
  const confirmRememberedSync = useCallback(async (latest: SourceResponse) => {
    if (syncBusy.current) return;
    syncBusy.current = true;
    setSyncing(true);
    // Show the remembered "on" as what is being applied, so a click now
    // means "turn it off" and the header and switch agree (D77).
    setSyncIntent(true);
    setSyncError("");
    try {
      await setSyncRef.current(latest, true);
      if (mounted.current && latestSource(latest).sync_enabled !== true)
        setSyncError(SYNC_UNCONFIRMED);
    } catch {
      if (mounted.current) setSyncError(SYNC_UNCONFIRMED);
    } finally {
      syncBusy.current = false;
      if (mounted.current) {
        setSyncing(false);
        if (syncWanted.current !== null) void drainSyncRef.current();
        else setSyncIntent(null);
      }
    }
  }, []);
  const readHost = useCallback(async () => {
    const version = ++epoch.current,
      id = source.source_id;
    if (!window.oneMemory) {
      setRead({ sourceId: id, state: "no-bridge" });
      return;
    }
    setRead({ sourceId: id, state: "loading" });
    try {
      const matched = matchHost(await refreshRecallHosts(), id);
      if (!mounted.current || version !== epoch.current) return;
      setRead({ sourceId: id, state: "ready" });
      setCheckedAt(new Date().toISOString());
      if (matched?.installed) void probeLink(matched.hostId);
      else setLink(null);
    } catch {
      if (mounted.current && version === epoch.current)
        setRead({ sourceId: id, state: "error" });
    }
  }, [probeLink, source.source_id]);
  useEffect(() => {
    void source.source_id;
    mounted.current = true;
    return () => {
      mounted.current = false;
      ++epoch.current;
    };
  }, [source.source_id]);
  useEffect(() => {
    syncApplyStarted.current = false;
    const owner = {};
    refreshFlight.current = owner;
    setRefreshing(true);
    setRefreshError(false);
    void readHost();
    void Promise.resolve()
      .then(() => refreshRef.current())
      .then(
        (result) => {
          if (!mounted.current || refreshFlight.current !== owner) return;
          const latest = result?.sources?.find(
            (item) => item.source_id === source.source_id,
          );
          if (
            latest &&
            latest.sync_enabled !== true &&
            latest.authorized &&
            latest.capabilities?.live_sync === "implemented" &&
            preferenceHasSource(
              readConnectionPreferences().syncSourceIds,
              source.source_id,
            ) &&
            !syncBusy.current &&
            !syncApplyStarted.current
          ) {
            syncApplyStarted.current = true;
            void confirmRememberedSync(latest);
          }
          refreshFlight.current = null;
          setRefreshing(false);
          setRefreshError(false);
        },
        () => {
          if (!mounted.current || refreshFlight.current !== owner) return;
          refreshFlight.current = null;
          setRefreshing(false);
          setRefreshError(true);
        },
      );
    return () => {
      refreshFlight.current = null;
    };
  }, [confirmRememberedSync, readHost, source.source_id]);
  async function reread() {
    if (refreshFlight.current || !mounted.current) return;
    const owner = {};
    refreshFlight.current = owner;
    setRefreshing(true);
    setRefreshError(false);
    const [sourceResult] = await Promise.allSettled([
      Promise.resolve().then(refresh),
      readHost(),
    ]);
    if (!mounted.current || refreshFlight.current !== owner) return;
    refreshFlight.current = null;
    setRefreshing(false);
    setRefreshError(sourceResult.status === "rejected");
  }
  const current = read.sourceId === source.source_id ? read : null;
  const host = matchHost(hosts, source.source_id);
  const loading = !current || current.state === "loading";
  const readFailed = current?.state === "error";
  const approved = hostConnectionApproved(host);
  const unsupported = hostConnectionUnsupported(host);
  const insertionChecked = insertionIntent ?? approved;
  const memoryChecked = memoryIntent ?? host?.permission?.state === "approved";
  const probing =
    refreshing ||
    installing ||
    memoryPending ||
    loading ||
    readFailed ||
    (stale && current?.state !== "ready");
  const probeState =
    link?.probe.state ??
    (linkPending || linkFailed ? null : (host?.selfCheck?.state ?? null));
  const stateText = connectionLabel({
    approved,
    insertionOn: insertionChecked,
    pending: probing || linkPending || linkFailed,
    failed: readFailed || linkFailed,
    probeState,
  });
  const hostId =
    host?.hostId ?? connectionHostId(source.source_id) ?? source.source_id;
  const hostError = installError
    ? installNoticeText(installError, language)
    : "";
  async function applyInsertion(enabled: boolean) {
    const bridge = window.oneMemory;
    if (!bridge) return;
    setInstallError(null);
    if (enabled) {
      await enableHostConnection(source.source_id);
      void probeLink(hostId);
      return;
    }
    const { outcome } = await disconnectHost(hostId);
    if (outcome.state === "failed")
      throw Object.assign(Error("disconnect_failed"), {
        reason: outcome.reason,
        rereadFailed: outcome.rereadFailed,
      });
    setLink(null);
    if (outcome.state === "review")
      setInstallError({
        kind: "review",
        name: source.display_name,
        revoked: outcome.permission?.receipt?.status === "revoked",
        findings: outcome.findings,
      });
    if (outcome.state === "revoked")
      setInstallError({
        kind: "revoked",
        name: source.display_name,
        configEdited: outcome.configEdited,
      });
  }
  async function changeInsertion(enabled: boolean) {
    writeConnectionPreference("host", source.source_id, enabled);
    noteUserHostChoice([source.source_id]);
    insertionWanted.current = enabled;
    setInsertionIntent(enabled);
    if (insertionBusy.current) return;
    insertionBusy.current = true;
    setInstalling(true);
    setInstallError(null);
    try {
      while (insertionWanted.current !== null && mounted.current) {
        const wanted = insertionWanted.current;
        insertionWanted.current = null;
        try {
          await applyInsertion(wanted);
        } catch (error) {
          if (error instanceof Error && error.message === "host_unsupported") {
            writeConnectionPreference("host", source.source_id, false);
            continue;
          }
          // A failed turn-on keeps the stored choice on; a failed turn-off
          // shows the grant that is really still there after the re-read.
          setInstallError(
            wanted
              ? {
                  kind: "connect",
                  reason: hostWriteReason(error),
                  rereadFailed: hostRereadFailed(error),
                }
              : {
                  kind: "disconnect",
                  name: source.display_name,
                  reason:
                    (error as { reason?: string | null }).reason ??
                    hostWriteReason(error),
                  rereadFailed:
                    (error as { rereadFailed?: boolean }).rereadFailed ?? false,
                },
          );
        }
      }
    } finally {
      insertionBusy.current = false;
      if (mounted.current) {
        setInstalling(false);
        setInsertionIntent(null);
      }
    }
  }
  async function changeSync(enabled: boolean) {
    if (stale && !syncWriteConfirmed) return;
    writeConnectionPreference("sync", source.source_id, enabled);
    if (!enabled) setBackfillNotice(false);
    syncWanted.current = enabled;
    setSyncIntent(enabled);
    await drainSync();
  }
  async function drainSync() {
    if (syncBusy.current) return;
    syncBusy.current = true;
    setSyncing(true);
    setSyncError("");
    let failure: string | null = null;
    try {
      while (syncWanted.current !== null && mounted.current) {
        const wanted = syncWanted.current;
        syncWanted.current = null;
        failure = null;
        try {
          const wasFrozen = !syncChecked;
          await setSync(source, wanted);
          if (wanted && wasFrozen) setBackfillNotice(true);
        } catch (reason) {
          failure =
            reason instanceof Error && reason.message
              ? reason.message
              : SYNC_UNCONFIRMED;
        }
      }
    } finally {
      syncBusy.current = false;
      if (mounted.current) {
        setSyncing(false);
        setSyncIntent(null);
        if (failure !== null) setSyncError(failure);
      }
    }
  }
  drainSyncRef.current = drainSync;
  const syncStatus = syncing
    ? syncChecked
      ? `${t("正在开启处理")}…`
      : `${t("正在冻结处理")}…`
    : syncChecked
      ? t("处理中")
      : t("已冻结");
  async function changeMemoryPermission(enabled: boolean) {
    if (!window.oneMemory) return;
    if (stale) return;
    if (!enabled) writeConnectionPreference("host", source.source_id, false);
    memoryWanted.current = enabled;
    setMemoryIntent(enabled);
    if (memoryBusy.current) return;
    memoryBusy.current = true;
    setMemoryPending(true);
    setInstallError(null);
    try {
      while (memoryWanted.current !== null && mounted.current) {
        const wanted = memoryWanted.current;
        memoryWanted.current = null;
        try {
          setInstallError(null);
          await setHostMemoryPermission({
            hostId,
            sourceId: source.source_id,
            enabled: wanted,
          });
          if (wanted && hostId === "cursor") {
            setHostRestartNotice(
              "Cursor 的连接凭证已更新。请完全退出并重新打开 Cursor，再开始新对话。",
            );
          } else if (!wanted) setHostRestartNotice("");
        } catch (error) {
          setInstallError({
            kind: "permission",
            reason: hostWriteReason(error),
            rereadFailed: hostRereadFailed(error),
          });
        }
      }
    } finally {
      memoryBusy.current = false;
      if (mounted.current) {
        setMemoryPending(false);
        setMemoryIntent(null);
      }
    }
  }
  return (
    <section
      className="r6-app-detail"
      aria-label={t("{name} 应用设置", { name: source.display_name })}
    >
      <h1 className="r6-app-detail-title">{t("应用详情")}</h1>
      <R6Button variant="quiet" onClick={onBack}>
        {t("返回应用")}
      </R6Button>
      <header>
        <span className="r6-app-detail-icon">
          <SourceIcon source={source} />
        </span>
        <div>
          <h2>{source.display_name}</h2>
          <p>{syncStatus}</p>
        </div>
      </header>
      <div className="r6-app-settings">
        <section className="r6-app-setting">
          <div>
            <h2>{t("同步开关")}</h2>
            <p>{t("开启后处理历史待办，并持续接收此应用的新会话。")}</p>
            {syncing && syncChecked && (
              <p role="status">
                {t(
                  "正在读取已有对话，首次开启可能需要一两分钟，请不要重复点击。",
                )}
              </p>
            )}
            {backfillNotice && (
              <p role="status">
                {t("将补充冻结期间的新对话；原有 pending Job 继续处理。")}
              </p>
            )}
            {!syncing && authorizationNote && (
              <p role="status">{authorizationNote}</p>
            )}
            {syncError && (
              <p role="alert">
                {syncError === SYNC_UNCONFIRMED || language === "en"
                  ? t(SYNC_UNCONFIRMED)
                  : syncError}
              </p>
            )}
          </div>
          <R6Switch
            label={t("同步开关")}
            checked={syncChecked}
            pending={syncing}
            disabled={
              (stale && !syncWriteConfirmed) ||
              source.capabilities?.live_sync !== "implemented"
            }
            onCheckedChange={(value) => void changeSync(value)}
          />
        </section>
        <section className="r6-app-setting">
          <div>
            <h2>{t("一键插入应用")}</h2>
            <p>{t("安装记忆连接，并默认允许此应用使用已授权记忆。")}</p>
            {unsupported && (
              <p>{`${source.display_name} ${t(UNSUPPORTED_NOTE)}`}</p>
            )}
            {hostError && <p role="alert">{hostError}</p>}
            {appLocationNote && <p role="status">{t(appLocationNote)}</p>}
            {hostRestartNotice ? (
              <p role="status">{t(hostRestartNotice)}</p>
            ) : null}
          </div>
          <R6Switch
            label={t("一键插入应用")}
            checked={insertionChecked}
            pending={installing}
            disabled={
              stale ||
              appLocationNote !== null ||
              unsupported ||
              source.capabilities?.installer !== "implemented" ||
              source.capabilities?.recall !== "implemented" ||
              !hostCapabilities().install
            }
            onCheckedChange={(value) => void changeInsertion(value)}
          />
        </section>
        {MEMORY_PERMISSION_SECTION_VISIBLE && (
          <section className="r6-app-setting">
            <div>
              <h2>{t("允许使用记忆")}</h2>
              <p>
                {t("让 {name} 查询已授权范围内的记忆。", {
                  name: source.display_name,
                })}
              </p>
            </div>
            <R6Switch
              label={t("允许使用记忆")}
              checked={memoryChecked}
              pending={memoryPending}
              disabled={
                stale ||
                !hostCapabilities().permission ||
                !hostCapabilities().revoke
              }
              onCheckedChange={(value) => void changeMemoryPermission(value)}
            />
          </section>
        )}
        <section className="r6-app-setting">
          <div>
            <h2>{t("连接状态")}</h2>
            {refreshError && (
              <p role="alert">
                {t("暂时无法刷新来源状态，请重试；未更改同步或读取权限。")}
              </p>
            )}
            {readFailed && (
              <p role="alert">
                {t("暂时无法读取连接状态，当前状态待核对；请重试。")}
              </p>
            )}
            {probeState && probeState !== "wired" && (
              <p>
                {t(
                  "记忆工具没有进到 {name} 当前对话。完全退出 {name} 后重新打开，新对话里才会出现。",
                  { name: source.display_name },
                )}
              </p>
            )}
            {probeState === "wired" && (
              <p>
                {t("已接通。已经打开的对话不会中途出现这个工具，请新开一轮。")}
              </p>
            )}
            {linkFailed && (
              <p role="alert">
                {t(
                  "本次连接探测失败，当前状态待核对；未把上次读取当作本次成功。",
                )}
              </p>
            )}
            {checkedAt && !stale && current?.state === "ready" && (
              <small>
                {t("上次读取：{time}", {
                  time: new Date(checkedAt).toLocaleString(language),
                })}
              </small>
            )}
          </div>
          <div className="r6-app-detail-actions">
            <button
              type="button"
              className="r6-app-detail-status"
              aria-busy={probing || undefined}
              onClick={() => void reread()}
            >
              <span>{t(stateText)}</span>
              {probing ? (
                <span className="r6-button-spinner" aria-hidden="true" />
              ) : null}
            </button>
          </div>
        </section>
      </div>
    </section>
  );
}
