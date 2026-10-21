import { formatUiNumber, type UiMessage } from "@one-memory/contracts";
import { Download, Flag, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  getDetailedLogging,
  getMemoryProcessing,
  setDetailedLogging,
} from "./api";
import {
  type BackgroundSnapshot,
  readBackgroundHealth,
} from "./background-health-read";
import { clockStamp } from "./clock-stamp";
import { exportDiagnosticsFile } from "./diagnostics-export";
import type { RecallHostStatus } from "./global";
import { refreshRecallHosts } from "./host-state";
import {
  blockReasonLabel,
  retryingBlockLabel,
  splitBlockedStages,
  taskKindLabel,
} from "./memory-processing-labels";
import { subscribePoll } from "./poll-scheduler";
import { R6Button, R6Switch } from "./R6Controls";
import { LINK_WORDING } from "./recall-host-wording";
import { useTranslate, useUiLanguage } from "./ui-language";
import type { useImportSources } from "./use-import-sources";

type ImportState = ReturnType<typeof useImportSources>;

type Reading<T> = {
  value: T | null;
  pending: boolean;
  confirmedAt: string | null;
  failedAt: string | null;
};

function useDiagnosticRead<T>(load: () => Promise<T>, pollMs?: number) {
  const [reading, setReading] = useState<Reading<T>>({
    value: null,
    pending: true,
    confirmedAt: null,
    failedAt: null,
  });
  const alive = useRef(false);
  const version = useRef(0);
  const pending = useRef(false);
  const read = useCallback(async () => {
    const request = ++version.current;
    pending.current = true;
    setReading((previous) => ({ ...previous, pending: true }));
    try {
      const value = await load();
      if (!alive.current || request !== version.current) return;
      setReading({
        value,
        pending: false,
        confirmedAt: new Date().toISOString(),
        failedAt: null,
      });
    } catch {
      if (!alive.current || request !== version.current) return;
      setReading((previous) => ({
        ...previous,
        pending: false,
        failedAt: new Date().toISOString(),
      }));
    } finally {
      if (alive.current && request === version.current) pending.current = false;
    }
  }, [load]);

  useEffect(() => {
    alive.current = true;
    void read();
    const stop =
      pollMs === undefined
        ? () => {}
        : subscribePoll({
            key: "health",
            intervalMs: pollMs,
            run: () => {
              if (!pending.current) return read();
              return undefined;
            },
          });
    return () => {
      alive.current = false;
      ++version.current;
      stop();
    };
  }, [read, pollMs]);
  return { ...reading, read };
}

async function readHosts() {
  if (!window.oneMemory) return "no-bridge" as const;
  return (await refreshRecallHosts()).filter((host) => host.present);
}

function ReadTime({ label, value }: { label: UiMessage; value: string }) {
  const t = useTranslate();
  const language = useUiLanguage();
  return (
    <p className="health-stamp">
      {t(label)}
      <time dateTime={value}>{clockStamp(value, language)}</time>
    </p>
  );
}

function ReadReceipt({
  pending,
  confirmedAt,
  failedAt,
}: Pick<Reading<unknown>, "pending" | "confirmedAt" | "failedAt">) {
  const t = useTranslate();
  return (
    <>
      {failedAt ? (
        <p role="status">
          {t(
            confirmedAt
              ? "暂时无法读取 · 数据已过期，以下为上次读取结果。"
              : "暂时无法读取。",
          )}
        </p>
      ) : null}
      {pending ? (
        <p role="status">
          {t(
            confirmedAt ? "正在重新读取，以下为上次读取结果…" : "正在读取状态…",
          )}
        </p>
      ) : null}
      {confirmedAt ? (
        <ReadTime label="上次成功读取" value={confirmedAt} />
      ) : null}
      {failedAt ? <ReadTime label="读取失败时间" value={failedAt} /> : null}
    </>
  );
}

/** Everything needed to answer "is it working" in one place.
 *
 * All four readings already existed, on three different screens, and none of
 * them is conclusive alone: the service can be up while the pipeline is
 * stalled, the pipeline can be running while every host reads nothing. The
 * cost of them being apart is that nobody thinks to put them together.
 *
 * Aggregation only — no endpoint is added, and every word here comes from
 * the table the originating screen already uses.
 */
export function HealthPanel({ imports }: { imports: ImportState }) {
  const t = useTranslate();
  const language = useUiLanguage();
  const number = (value: number) => formatUiNumber(language, value);
  const [detailedLogging, setDetailedLoggingState] = useState(false);
  const [detailedLoggingPending, setDetailedLoggingPending] = useState(false);
  const backgroundRead = useDiagnosticRead(readBackgroundHealth);
  const processingRead = useDiagnosticRead(getMemoryProcessing, 5_000);
  const hostsRead = useDiagnosticRead(readHosts);
  const processing = processingRead.value;
  const hosts = hostsRead.value;
  const [refreshing, setRefreshing] = useState(false);
  const [diagnosticAction, setDiagnosticAction] = useState<
    "incident" | "export" | null
  >(null);
  const [diagnosticMessage, setDiagnosticMessage] = useState<{
    key: UiMessage;
    values?: Readonly<Record<string, string>>;
  } | null>(null);
  const [sourceFailedAt, setSourceFailedAt] = useState<string | null>(null);
  const refreshFlight = useRef(false);
  const lifecycle = useRef(0);

  useEffect(() => {
    void getDetailedLogging()
      .then(setDetailedLoggingState)
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    ++lifecycle.current;
    return () => {
      ++lifecycle.current;
    };
  }, []);

  async function refresh() {
    if (refreshFlight.current) return;
    refreshFlight.current = true;
    setRefreshing(true);
    const session = lifecycle.current;
    const sourceRead = async () => {
      try {
        await imports.refresh();
        if (session === lifecycle.current) setSourceFailedAt(null);
      } catch {
        if (session === lifecycle.current)
          setSourceFailedAt(new Date().toISOString());
      }
    };
    await Promise.all([
      backgroundRead.read(),
      processingRead.read(),
      hostsRead.read(),
      sourceRead(),
    ]);
    if (session !== lifecycle.current) return;
    refreshFlight.current = false;
    setRefreshing(false);
  }

  async function markIncident() {
    if (!window.oneMemory?.markDiagnosticIncident) {
      setDiagnosticMessage({ key: "请在桌面 App 中记录问题时间点。" });
      return;
    }
    setDiagnosticAction("incident");
    setDiagnosticMessage(null);
    try {
      const description = window.prompt(
        t("简单描述刚才遇到的问题（可不填）"),
        "",
      );
      if (description === null) {
        setDiagnosticAction(null);
        return;
      }
      const receipt =
        await window.oneMemory.markDiagnosticIncident(description);
      await window.oneMemory.copyText?.(receipt.incident_id);
      setDiagnosticMessage({
        key: "问题编号已复制：{id}",
        values: { id: receipt.incident_id },
      });
    } catch {
      setDiagnosticMessage({ key: "问题时间点未能记录，请重试。" });
    } finally {
      setDiagnosticAction(null);
    }
  }

  async function exportDiagnostics() {
    if (!window.oneMemory?.exportDiagnostics) {
      setDiagnosticMessage({ key: "请在桌面 App 中导出诊断信息。" });
      return;
    }
    setDiagnosticAction("export");
    setDiagnosticMessage(null);
    try {
      const result = await exportDiagnosticsFile();
      setDiagnosticMessage(
        result.ok
          ? {
              key: "日志已导出：{name}",
              values: result.filename ? { name: result.filename } : undefined,
            }
          : { key: "诊断信息未能导出，请重试。" },
      );
    } finally {
      setDiagnosticAction(null);
    }
  }

  // The shared snapshot distinguishes read failures from unrelated action errors.
  const sourceUnavailable =
    imports.sourceSnapshot?.stale ??
    (Boolean(imports.error) || sourceFailedAt !== null);
  const sourceConfirmedAt = imports.sourceSnapshot?.confirmedAt;
  const sourceReceipt = (
    <>
      {imports.loading ? (
        <p role="status">{t("正在读取本地服务与来源状态…")}</p>
      ) : sourceUnavailable ? (
        <p role="status">
          {t(
            sourceConfirmedAt || imports.health || imports.sources.length
              ? "暂时无法读取 · 数据已过期，以下为上次读取结果。"
              : "暂时无法读取。",
          )}
        </p>
      ) : null}
      {sourceConfirmedAt ? (
        <ReadTime label="上次成功读取" value={sourceConfirmedAt} />
      ) : null}
      {sourceUnavailable && sourceFailedAt ? (
        <ReadTime label="读取失败时间" value={sourceFailedAt} />
      ) : null}
    </>
  );

  // Retries are summed rather than listed. This panel is where a developer
  // looks, and even here the pipeline's internal guard names are not what a
  // person reads: the count says how much is in flight, and the code that
  // failed is on the task row in Core.
  const retrying = processing
    ? splitBlockedStages(processing.blocked_stages).retrying
    : 0;

  return (
    <section className="view health-view" aria-labelledby="health-title">
      <header className="view-header">
        <div>
          <h1 id="health-title">{t("运行诊断")}</h1>
        </div>
      </header>

      <div className="health-actions health-actions-primary">
        <R6Button
          variant="primary"
          pending={diagnosticAction === "export"}
          onClick={() => void exportDiagnostics()}
        >
          <Download size={16} aria-hidden="true" />
          {t("导出日志（发给开发排查）")}
        </R6Button>
      </div>

      <section className="health-block" aria-label={t("本地服务")}>
        <h2>{t("本地服务")}</h2>
        {sourceReceipt}
        {imports.health ? (
          <p>
            {t(
              sourceUnavailable || imports.loading
                ? "上次读取 · 版本 {version} · 实例 {id}"
                : "运行中 · 版本 {version} · 实例 {id}",
              {
                version: imports.health.version,
                id: imports.health.instance_id,
              },
            )}
          </p>
        ) : !sourceUnavailable && !imports.loading ? (
          <p>{t("还没连上本地 Core。")}</p>
        ) : null}
      </section>

      <section className="health-block" aria-label={t("后台服务")}>
        <h2>{t("后台服务")}</h2>
        {backgroundRead.value === "no-bridge" ? (
          <p>
            {t("当前环境无法检查后台服务，请在支持此功能的桌面 App 中查看。")}
          </p>
        ) : (
          <>
            <ReadReceipt {...backgroundRead} />
            {backgroundRead.value ? (
              <>
                <p>
                  {backgroundRead.pending || backgroundRead.failedAt
                    ? t("上次读取：")
                    : ""}
                  {t(backgroundVerdict(backgroundRead.value.state))}
                </p>
                <p>
                  {t("Core：{state}", {
                    state: t(runtimeStateLabel(backgroundRead.value.core)),
                  })}{" "}
                  ·{" "}
                  {t("宿主桥接：{state}", {
                    state: t(runtimeStateLabel(backgroundRead.value.broker)),
                  })}
                </p>
              </>
            ) : null}
          </>
        )}
      </section>

      <section className="health-block" aria-label={t("各来源累计条数")}>
        <h2>{t("各来源累计条数")}</h2>
        {sourceReceipt}
        {imports.sources.length ? (
          <dl>
            {imports.sources.map((source) => (
              <div key={source.source_id}>
                <dt>{source.display_name}</dt>
                <dd>
                  {t("{conversations} 个会话 · {messages} 条消息", {
                    conversations: number(source.imported_conversations),
                    messages: number(source.imported_messages),
                  })}
                </dd>
              </div>
            ))}
          </dl>
        ) : !sourceUnavailable && !imports.loading ? (
          <p>{t("还没有已连接的来源。")}</p>
        ) : null}
      </section>

      <section className="health-block" aria-label={t("记忆提炼")}>
        <h2>{t("记忆提炼")}</h2>
        <ReadReceipt {...processingRead} />
        {processing ? (
          <>
            <p>
              {t(
                "{running} 处理中 · {pending} 排队 · {completed} 已完成 · {failed} 失败",
                {
                  running: number(processing.running),
                  pending: number(processing.pending),
                  completed: number(processing.completed),
                  failed: number(processing.failed),
                },
              )}
            </p>
            {processing.metrics ? (
              <dl aria-label={t("导入与整理详情")}>
                <div>
                  <dt>{t("原文与完整对话轮次")}</dt>
                  <dd>
                    {t("{raw} 条原文 · {turns} 个完整 turn", {
                      raw: number(processing.metrics.raw_evidence),
                      turns: number(processing.metrics.complete_turns),
                    })}
                  </dd>
                </div>
                <div>
                  <dt>{t("HY 整理")}</dt>
                  <dd>
                    {t(
                      "{calls} 次调用 · {completed} 完成 · {unknown} 结果不确定 · {failed} 失败 · {cancelled} 取消",
                      {
                        calls: number(processing.metrics.hy_calls),
                        completed: number(processing.metrics.completed),
                        unknown: number(processing.metrics.unknown),
                        failed: number(processing.metrics.failed),
                        cancelled: number(processing.metrics.cancelled),
                      },
                    )}
                  </dd>
                </div>
                <div>
                  <dt>{t("产出")}</dt>
                  <dd>
                    {t("{count} 条记忆", {
                      count: number(processing.metrics.governed_memories),
                    })}
                  </dd>
                </div>
                <div>
                  <dt>{t("WorkBuddy 查记忆")}</dt>
                  <dd>
                    {t(
                      "{injected} 次注入 · {timeout} 次超时 · {unavailable} 次不可用",
                      {
                        injected: number(processing.metrics.workbuddy.injected),
                        timeout: number(processing.metrics.workbuddy.timed_out),
                        unavailable: number(
                          processing.metrics.workbuddy.unavailable,
                        ),
                      },
                    )}
                  </dd>
                </div>
              </dl>
            ) : null}
            {processing.blocked_stages.length ? (
              <ul>
                {splitBlockedStages(processing.blocked_stages).named.map(
                  (block) => (
                    <li key={`${block.kind}:${block.reason}`}>
                      {t("{kind}卡在 {reason}（{count} 条）", {
                        kind: taskKindLabel(block.kind, language),
                        reason: blockReasonLabel(block.reason, language),
                        count: number(block.count),
                      })}
                    </li>
                  ),
                )}
                {retrying ? (
                  <li>{retryingBlockLabel(retrying, language)}</li>
                ) : null}
              </ul>
            ) : !processingRead.failedAt && !processingRead.pending ? (
              <p>{t("暂无需要处理的问题。")}</p>
            ) : null}
          </>
        ) : null}
      </section>

      <section className="health-block" aria-label={t("AI 接通情况")}>
        <h2>{t("AI 接通情况")}</h2>
        {/* Host state lives in the desktop shell. In a browser it is not
            false, it is unavailable, and reporting it as 未接通 would send
            the user to repair links that may well be fine. */}
        {hosts === "no-bridge" || !window.oneMemory ? (
          <p>{t("在浏览器里读不到；请在桌面 App 里查看。")}</p>
        ) : (
          <>
            <ReadReceipt {...hostsRead} />
            {hosts?.length ? (
              <dl>
                {hosts.map((host) => (
                  <div key={host.hostId}>
                    <dt>{host.label}</dt>
                    <dd>
                      {hostsRead.failedAt || hostsRead.pending
                        ? t("上次读取：")
                        : ""}
                      {t(hostVerdict(host))}
                    </dd>
                  </div>
                ))}
              </dl>
            ) : hosts && !hostsRead.failedAt && !hostsRead.pending ? (
              <p>{t("本机没有检测到可接入的 AI 工具。")}</p>
            ) : null}
          </>
        )}
      </section>
      <section className="health-block" aria-label={t("详细日志")}>
        <div className="health-setting-row">
          <div>
            <h2>{t("详细日志（排查用）")}</h2>
            <p>{t("默认关闭。仅在开发人员请你协助排查时开启。")}</p>
          </div>
          <R6Switch
            label={t("详细日志（排查用）")}
            checked={detailedLogging}
            pending={detailedLoggingPending}
            onCheckedChange={(enabled) => {
              setDetailedLoggingPending(true);
              void setDetailedLogging(enabled)
                .then(setDetailedLoggingState)
                .catch(() =>
                  setDiagnosticMessage({ key: "详细日志设置失败，请重试。" }),
                )
                .finally(() => setDetailedLoggingPending(false));
            }}
          />
        </div>
      </section>
      <div className="health-actions">
        <R6Button
          variant="primary"
          pending={refreshing}
          onClick={() => void refresh()}
        >
          <RefreshCw size={16} aria-hidden="true" />
          {t("重新读取状态")}
        </R6Button>
        <R6Button
          variant="quiet"
          pending={diagnosticAction === "incident"}
          onClick={() => void markIncident()}
        >
          <Flag size={16} aria-hidden="true" />
          {t("记录问题时间点")}
        </R6Button>
      </div>
      {diagnosticMessage ? (
        <p className="health-diagnostic-message" role="status">
          {t(
            diagnosticMessage.key,
            diagnosticMessage.key === "日志已导出：{name}" &&
              !diagnosticMessage.values
              ? { name: t("诊断文件") }
              : diagnosticMessage.values,
          )}
        </p>
      ) : null}
    </section>
  );
}

/** A host with no self-check on record has not been shown to work, and the
 * install flow's own wording for that is 未接通. */
function hostVerdict(host: RecallHostStatus): UiMessage {
  if (!host.installed) return LINK_WORDING.unreachable;
  const state = host.selfCheck?.state;
  return (state ? LINK_WORDING[state] : undefined) ?? LINK_WORDING.unreachable;
}

function backgroundVerdict(state: BackgroundSnapshot["state"]): UiMessage {
  return {
    healthy: "后台服务可用",
    stopped: "后台服务已停止",
    degraded: "后台服务部分不可用",
    untrusted: "后台服务身份未通过核验",
    unknown: "后台状态尚未确认",
  }[state] as UiMessage;
}

function runtimeStateLabel(
  state: BackgroundSnapshot["core"] | BackgroundSnapshot["broker"],
): UiMessage {
  return {
    running: "运行中",
    stopped: "已停止",
    unhealthy: "无法连接",
    untrusted: "身份未通过核验",
    unknown: "尚未确认",
    not_checked: "未检查",
  }[state] as UiMessage;
}
