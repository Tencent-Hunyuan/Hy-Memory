import type { SourceResponse } from "@one-memory/contracts";
import type { MessageKey } from "@one-memory/i18n";
import { useCallback, useEffect, useRef, useState } from "react";
import { AppMark } from "./app-mark";
import { connectionHostId, preferenceHasSource } from "./connection-hosts";
import {
  readConnectionPreferences,
  writeConnectionPreference,
} from "./connection-preferences";
import { noteUserHostChoice } from "./connection-reconcile";
import type { RecallLinkCheck } from "./global";
import {
  disconnectHost,
  enableHostConnection,
  hostCapabilities,
  hostConnectionApproved,
  hostConnectionUnsupported,
  hostRereadFailed,
  hostWriteReason,
  matchHost,
  probeHost,
  refreshRecallHosts,
  setHostMemoryPermission,
  UNSUPPORTED_NOTE,
  useRecallHosts,
} from "./host-state";
import { useOnboardingTranslation } from "./onboarding-language";
import { R6Button, R6Switch } from "./R6Controls";
import {
  latestSource,
  sourceAuthorizationNote,
  useSource,
} from "./source-state";
import "./application-detail.css";

/** A failed host write as data: worded only when rendered. */
type HostFailure = { reason: string | null; rereadFailed: boolean };
type InstallError = {
  key: MessageKey;
  params: Record<string, string>;
  failure?: HostFailure;
};

const SYNC_UNCONFIRMED = "此应用的处理开关结果未能确认，其他应用不受影响。";
// D97: the single sync switch now governs both taking in new data and
// generating memories, so the separate read-permission switch is hidden, not
// removed — flip this back to restore it without digging through history.
const MEMORY_PERMISSION_SECTION_VISIBLE = false;

function connectionLabel(input: {
  approved: boolean;
  insertionOn: boolean;
  pending: boolean;
  failed: boolean;
  probeState: string | null;
}) {
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
  const { text, t, locale } = useOnboardingTranslation();
  /** Word a host failure at render time so a language switch re-words it. */
  function failureNote({ reason, rereadFailed }: HostFailure) {
    return `${reason ? t("appFailureReason", { reason: text(reason) }) : ""}${
      rereadFailed ? t("appFailureReread") : ""
    }`;
  }
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
  const [installError, setInstallError] = useState<string | InstallError>("");
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState("");
  const [memoryPending, setMemoryPending] = useState(false);
  const [hostRestartNotice, setHostRestartNotice] = useState("");
  const [syncIntent, setSyncIntent] = useState<boolean | null>(null);
  // D97: shown after the switch is turned back on, so the person knows the
  // conversations missed while frozen will be filled in by a backfill import.
  const [backfillNotice, setBackfillNotice] = useState(false);
  const [appLocationNote, setAppLocationNote] = useState<string | null>(null);
  const syncChecked = syncIntent ?? source.sync_enabled === true;
  // The sync switch is the source authorization control, so the only note
  // beside it is about the source. Plugin trouble belongs to the other switch.
  const authorizationNote = sourceAuthorizationNote(source);
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
  const hostError =
    typeof installError === "string"
      ? text(installError)
      : t(installError.key, {
          ...installError.params,
          ...(installError.failure
            ? { reason: failureNote(installError.failure) }
            : {}),
        });
  async function applyInsertion(enabled: boolean) {
    const bridge = window.oneMemory;
    if (!bridge) return;
    setInstallError("");
    if (enabled) {
      await enableHostConnection(source.source_id);
      void probeLink(hostId);
      return;
    }
    const { outcome } = await disconnectHost(hostId);
    if (outcome.state === "failed")
      throw Object.assign(Error("disconnect_failed"), {
        failure: {
          reason: outcome.reason,
          rereadFailed: outcome.rereadFailed,
        } satisfies HostFailure,
      });
    setLink(null);
    if (outcome.state === "review")
      setInstallError({
        key:
          outcome.permission?.receipt?.status === "revoked"
            ? "appRevokedReview"
            : "appRemovalReview",
        params: {
          name: source.display_name,
          paths: outcome.findings
            .map((finding) => `${finding.path}（${finding.where}）`)
            .join("；"),
        },
      });
    if (outcome.state === "revoked")
      setInstallError({
        key: outcome.configEdited ? "appRevokedModified" : "appRevokedRetained",
        params: { name: source.display_name },
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
    setInstallError("");
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
                  key: "appConnectFailed",
                  params: {},
                  failure: {
                    reason: hostWriteReason(error),
                    rereadFailed: hostRereadFailed(error),
                  },
                }
              : {
                  key: "appDisconnectFailed",
                  params: { name: source.display_name },
                  failure: (error as { failure?: HostFailure }).failure ?? {
                    reason: null,
                    rereadFailed: false,
                  },
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
      ? "正在开启处理…"
      : "正在冻结处理…"
    : syncChecked
      ? "处理中"
      : "已冻结";
  async function changeMemoryPermission(enabled: boolean) {
    if (!window.oneMemory) return;
    if (stale) return;
    if (!enabled) writeConnectionPreference("host", source.source_id, false);
    memoryWanted.current = enabled;
    setMemoryIntent(enabled);
    if (memoryBusy.current) return;
    memoryBusy.current = true;
    setMemoryPending(true);
    setInstallError("");
    try {
      while (memoryWanted.current !== null && mounted.current) {
        const wanted = memoryWanted.current;
        memoryWanted.current = null;
        try {
          setInstallError("");
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
            key: "appPermissionFailed",
            params: {},
            failure: {
              reason: hostWriteReason(error),
              rereadFailed: hostRereadFailed(error),
            },
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
      aria-label={t("appSettingsTitle", { name: source.display_name })}
    >
      <h1 className="r6-app-detail-title">{text("应用详情")}</h1>
      <R6Button variant="quiet" onClick={onBack}>
        {text("返回应用")}
      </R6Button>
      <header>
        <span className="r6-app-detail-icon">
          <SourceIcon source={source} />
        </span>
        <div>
          <h2>{source.display_name}</h2>
          <p>{text(syncStatus)}</p>
        </div>
      </header>
      <div className="r6-app-settings">
        <section className="r6-app-setting">
          <div>
            <h2>{text("同步开关")}</h2>
            <p>{text("开启后处理历史待办，并持续接收此应用的新会话。")}</p>
            {syncing && syncChecked && (
              <p role="status">
                {text(
                  "正在读取已有对话，首次开启可能需要一两分钟，请不要重复点击。",
                )}
              </p>
            )}
            {backfillNotice && (
              <p role="status">
                {text("将补充冻结期间的新对话；原有 pending Job 继续处理。")}
              </p>
            )}
            {!syncing && authorizationNote && (
              <p role="status">{text(authorizationNote)}</p>
            )}
            {syncError && <p role="alert">{text(syncError)}</p>}
          </div>
          <R6Switch
            label={text("同步开关")}
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
            <h2>{text("一键插入应用")}</h2>
            <p>{text("安装记忆连接，并默认允许此应用使用已授权记忆。")}</p>
            {unsupported && (
              <p>{`${source.display_name} ${text(UNSUPPORTED_NOTE)}`}</p>
            )}
            {hostError && <p role="alert">{hostError}</p>}
            {appLocationNote && <p role="status">{text(appLocationNote)}</p>}
            {hostRestartNotice ? (
              <p role="status">{text(hostRestartNotice)}</p>
            ) : null}
          </div>
          <R6Switch
            label={text("一键插入应用")}
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
              <h2>{text("允许使用记忆")}</h2>
              <p>{t("appMemoryScope", { name: source.display_name })}</p>
            </div>
            <R6Switch
              label={text("允许使用记忆")}
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
            <h2>{text("连接状态")}</h2>
            {refreshError && (
              <p role="alert">
                {text("暂时无法刷新来源状态，请重试；未更改同步或读取权限。")}
              </p>
            )}
            {readFailed && (
              <p role="alert">
                {text("暂时无法读取连接状态，当前状态待核对；请重试。")}
              </p>
            )}
            {probeState && probeState !== "wired" && (
              <p>{t("appRestart", { name: source.display_name })}</p>
            )}
            {probeState === "wired" && (
              <p>
                {text(
                  "已接通。已经打开的对话不会中途出现这个工具，请新开一轮。",
                )}
              </p>
            )}
            {linkFailed && (
              <p role="alert">
                {text(
                  "本次连接探测失败，当前状态待核对；未把上次读取当作本次成功。",
                )}
              </p>
            )}
            {checkedAt && !stale && current?.state === "ready" && (
              <small>
                {text("上次读取")}: {new Date(checkedAt).toLocaleString(locale)}
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
              <span>{text(stateText)}</span>
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
