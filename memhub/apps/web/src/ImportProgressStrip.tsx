import {
  formatUiNumber,
  type ImportIssue,
  type ImportJob,
  type SourceResponse,
  translate,
  type UiLanguage,
  type UiMessage,
} from "@one-memory/contracts";
import { LoaderCircle, Pause, Play, RefreshCw, Settings2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  type MemoryArrival,
  memoryArrivalTitle,
  resolveMemoryArrival,
} from "./memory-arrival-state";
import {
  blockReasonLabel,
  importIssueLabel,
  taskKindLabel,
} from "./memory-processing-labels";
import {
  type SourceJobActionsProps,
  settledAction,
  validReceipt,
} from "./SourceJobActions";
import { useTranslate, useUiLanguage } from "./ui-language";
import "./import-progress-strip.css";

const RUNNING = new Set(["running"]);
const PAUSABLE = new Set(["pending", "running"]);
const RESUMABLE = new Set(["paused", "interrupted"]);

type JobAction = "pause" | "resume";
type JobFailure = {
  action: JobAction;
  failures: { job: ImportJob; message: string; internal?: UiMessage }[];
};

/** What a job's stored status means, in the reader's own language.
 *
 * The receipts used to print `running` and `completed` between two job ids.
 * A reader looking at their own import does not need the pipeline's enum; they
 * need to know whether it finished. */
const JOB_STATUS_LABELS: Record<string, UiMessage> = {
  pending: "等待中",
  running: "进行中",
  completed: "已完成",
  failed: "失败",
  paused: "已暂停",
  interrupted: "已中断",
  cancelled: "已停止",
};

export function jobStatusLabel(
  status: string,
  language: UiLanguage = "zh-CN",
): string {
  return JOB_STATUS_LABELS[status]
    ? translate(language, JOB_STATUS_LABELS[status])
    : status;
}

export interface ImportProgressStripProps {
  jobs: ImportJob[];
  sources: SourceResponse[];
  backfilling: boolean;
  onOpenSources(): void;
  sourceSnapshot?: { confirmedAt: string | null; stale: boolean };
  onRefresh?(): Promise<unknown>;
  arrival?: MemoryArrival;
  onConfigureModel?(): void;
  /** Pauses or continues one job and resolves with its receipt. */
  onJobAction?: SourceJobActionsProps["onAction"];
  issues?: readonly ImportIssue[];
  /** Settings keeps the status line and hides these two controls. */
  showSourceLink?: boolean;
  showProgressDetails?: boolean;
  userFacing?: boolean;
}

/** Named sources and confirmed counts, without claiming cached jobs are live. */
export function ImportProgressStrip({
  jobs,
  sources,
  backfilling,
  onOpenSources,
  sourceSnapshot,
  onRefresh,
  arrival,
  onConfigureModel,
  onJobAction,
  issues,
  showSourceLink = true,
  showProgressDetails = true,
  userFacing = false,
}: ImportProgressStripProps) {
  const t = useTranslate();
  const language = useUiLanguage();
  const number = (value: number) => formatUiNumber(language, value);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<{
    epoch: number;
    message: string;
  } | null>(null);
  const flight = useRef(false);
  const [jobFlight, setJobFlight] = useState<JobAction | null>(null);
  const [jobFailure, setJobFailure] = useState<JobFailure | null>(null);
  const jobsInFlight = useRef(false);
  const mounted = useRef(false);
  const stale = sourceSnapshot?.stale === true;
  const confirmedAt = sourceSnapshot?.confirmedAt;
  const hasConfirmedTime =
    typeof confirmedAt === "string" && Number.isFinite(Date.parse(confirmedAt));
  const snapshotKey = JSON.stringify([confirmedAt ?? null, stale]);
  const snapshot = useRef({ key: snapshotKey, epoch: 0 });
  if (snapshot.current.key !== snapshotKey) {
    snapshot.current = { key: snapshotKey, epoch: snapshot.current.epoch + 1 };
  }
  const visibleError =
    stale && refreshError?.epoch === snapshot.current.epoch
      ? refreshError.message
      : null;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  async function refresh() {
    if (flight.current || !onRefresh) return;
    const epoch = snapshot.current.epoch;
    flight.current = true;
    setRefreshing(true);
    setRefreshError(null);
    try {
      // The controller certifies source snapshots. Callback resolution is not proof.
      await onRefresh();
    } catch (reason) {
      if (mounted.current && snapshot.current.epoch === epoch) {
        setRefreshError({
          epoch,
          message:
            reason instanceof Error && reason.message.trim()
              ? reason.message
              : "",
        });
      }
    } finally {
      flight.current = false;
      if (mounted.current) setRefreshing(false);
    }
  }

  /** One job at a time, each checked against its own receipt. */
  async function changeJobs(action: JobAction, targets: ImportJob[]) {
    if (jobsInFlight.current || !onJobAction || !targets.length) return;
    jobsInFlight.current = true;
    setJobFlight(action);
    setJobFailure(null);
    const failures: JobFailure["failures"] = [];
    for (const target of targets) {
      try {
        const result = await onJobAction(target.job_id, action);
        if (!validReceipt(result, target)) {
          failures.push({
            job: target,
            message: "",
            internal: "未收到此任务的有效回执。",
          });
        } else if (!settledAction(action, result)) {
          failures.push({
            job: target,
            message: "",
            internal: "后台尚未确认此操作完成。",
          });
        }
      } catch (reason) {
        failures.push({
          job: target,
          message:
            reason instanceof Error && reason.message.trim()
              ? reason.message
              : "",
          internal:
            reason instanceof Error && reason.message.trim()
              ? undefined
              : "未能确认操作结果。",
        });
      }
    }
    jobsInFlight.current = false;
    if (!mounted.current) return;
    setJobFlight(null);
    setJobFailure(failures.length ? { action, failures } : null);
  }

  const nameOf = (sourceId: string) =>
    sources.find((item) => item.source_id === sourceId)?.display_name ??
    sourceId;

  const latest = latestBySource(jobs);
  const running = latest.filter((item) => RUNNING.has(item.status));
  const runningHistory = running.filter((item) => !isSyncJob(item));
  const runningSync = running.filter(isSyncJob);
  const pending = latest.filter((item) => item.status === "pending");
  const failed = latest.filter((item) => item.status === "failed");
  const completedWithErrors = latest.filter(
    (item) => item.status === "completed" && item.errors > 0,
  );
  const paused = latest.filter((item) =>
    ["paused", "interrupted", "cancelled"].includes(item.status),
  );
  const history = latest.filter((item) => !isSyncJob(item));
  const pausable = history.filter((item) => PAUSABLE.has(item.status));
  const resumable = history.filter((item) => RESUMABLE.has(item.status));
  const jobAction: JobAction | null = pausable.length
    ? "pause"
    : resumable.length
      ? "resume"
      : null;
  const importPaused =
    !stale &&
    !pausable.length &&
    history.some((item) => item.status === "paused");
  const retryTargets = (jobFailure?.failures ?? []).flatMap(({ job: failed }) =>
    history.filter(
      (item) =>
        item.job_id === failed.job_id &&
        (jobFailure?.action === "pause" ? PAUSABLE : RESUMABLE).has(
          item.status,
        ),
    ),
  );
  const currentArrival = arrival
    ? resolveMemoryArrival({ arrival, processing: null, sources, loaded: true })
    : null;
  const needsAction =
    currentArrival &&
    [
      "model-required",
      "failed",
      "stopped",
      "idle",
      "checking",
      "unavailable",
    ].includes(currentArrival.phase);
  const generating = !stale && currentArrival?.phase === "processing";
  const importing = !stale && running.length > 0;
  const progressing =
    !stale &&
    !needsAction &&
    (importing ||
      backfilling ||
      generating ||
      currentArrival?.phase === "importing");

  if (userFacing) {
    const firstImport = !stale && runningHistory.length > 0;
    const actionable =
      currentArrival !== null &&
      ["model-required", "failed", "stopped", "unavailable"].includes(
        currentArrival.phase,
      );
    if (
      !firstImport &&
      !actionable &&
      !failed.length &&
      !completedWithErrors.length
    ) {
      return null;
    }
    const imported = sources.reduce(
      (sum, source) => sum + source.imported_conversations,
      0,
    );
    const message = firstImport
      ? t(
          "正在导入你的历史对话，已导入 {count} 个会话。记忆会陆续生成，你可以先正常使用。",
          { count: number(imported) },
        )
      : currentArrival?.phase === "model-required"
        ? t("模型还没有配置好，新的对话暂时不会整理成记忆。")
        : t("有些内容没有处理完成，请前往设置查看。");
    return (
      <div
        className="import-strip"
        role="status"
        aria-live="polite"
        data-feedback="compact"
      >
        <div className="import-strip-headline">
          <div className="import-strip-heading">
            {firstImport && (
              <span className="import-strip-pulse" aria-hidden="true" />
            )}
            <strong>{message}</strong>
          </div>
          {!firstImport && onConfigureModel && (
            <button
              type="button"
              className="secondary"
              onClick={onConfigureModel}
            >
              <Settings2 size={15} aria-hidden="true" />
              {t("去设置")}
            </button>
          )}
        </div>
      </div>
    );
  }

  if (
    !stale &&
    !running.length &&
    !pending.length &&
    !failed.length &&
    !paused.length &&
    !completedWithErrors.length &&
    !backfilling &&
    (!currentArrival || currentArrival.phase === "idle")
  )
    return null;

  const knownSources = sources.filter(
    (source) =>
      source.authorized ||
      source.imported_messages > 0 ||
      latest.some((item) => item.source_id === source.source_id),
  );
  const confirmedMessages = knownSources.reduce(
    (sum, source) => sum + source.imported_messages,
    0,
  );
  const confirmedConversations = knownSources.reduce(
    (sum, source) => sum + source.imported_conversations,
    0,
  );
  const savedInLatestJobs = latest.reduce((sum, item) => sum + item.stored, 0);
  const canShowCounts =
    (!stale || hasConfirmedTime) &&
    (knownSources.length > 0 || latest.length > 0);
  const updating = stale ? refreshing : currentArrival?.refreshing === true;
  const title = stale
    ? updating
      ? t("已保存内容保留，正在核对进度")
      : t("进度待更新")
    : currentArrival && needsAction
      ? t(
          currentArrival.phase === "model-required"
            ? "待继续"
            : memoryArrivalTitle(currentArrival),
        )
      : generating
        ? t("正在沉淀记忆")
        : importing || currentArrival?.phase === "importing"
          ? running.length > 0 && running.every(isSyncJob)
            ? t("正在检查新增对话")
            : t("正在导入对话")
          : backfilling
            ? t("正在准备整理")
            : t("待继续");

  return (
    <div
      className="import-strip"
      role="status"
      aria-live="polite"
      aria-busy={refreshing}
      data-stale={stale}
      data-feedback="compact"
    >
      <div className="import-strip-headline">
        <div className="import-strip-heading">
          {progressing && (
            <span className="import-strip-pulse" aria-hidden="true" />
          )}
          <strong>{title}</strong>
          {canShowCounts && (
            <span className="import-strip-counts">
              {confirmedMessages > 0
                ? t("已保存 {messages} 条原文 · {conversations} 个会话", {
                    messages: number(confirmedMessages),
                    conversations: number(confirmedConversations),
                  })
                : t("本次已保存 {count} 条原文", {
                    count: number(savedInLatestJobs),
                  })}
              {currentArrival?.memoryCount !== undefined &&
              Number.isFinite(currentArrival.memoryCount) &&
              currentArrival.memoryCount >= 0
                ? t(" · {count} 条记忆", {
                    count: number(currentArrival.memoryCount),
                  })
                : ""}
            </span>
          )}
        </div>
        <span className="import-strip-support">
          {stale ||
          currentArrival?.phase === "unavailable" ||
          currentArrival?.phase === "checking"
            ? t("已保存内容保留。")
            : currentArrival?.phase === "model-required"
              ? t("请核对整理模型，已保存原文保留在本机。")
              : importPaused
                ? t("导入已暂停，已保存内容保留，随时可以继续。")
                : generating
                  ? t("先整理近期对话，更多记忆会陆续出现")
                  : currentArrival?.phase === "failed" ||
                      currentArrival?.phase === "stopped"
                    ? importing
                      ? t("原文仍在导入，记忆整理待继续；已保存内容保留。")
                      : t("已保存内容保留，可继续整理。")
                    : importing || currentArrival?.phase === "importing"
                      ? t("正在保存原文；导入进度不代表记忆生成进度。")
                      : backfilling
                        ? t(
                            "正在准备已授权的对话，尚未确认有运行中的生成任务。",
                          )
                        : t("已保存内容保留。")}
        </span>
      </div>

      {showProgressDetails && (
        <details className="memory-feedback-details import-strip-details">
          <summary>{t("查看进度与详情")}</summary>
          <div className="import-strip-body">
            {stale && (
              <div className="import-strip-snapshot">
                <strong>{t("来源状态待核对")}</strong>
                {hasConfirmedTime ? (
                  <>
                    <span className="import-strip-confirmed-time">
                      {t("上次确认来源状态：")}
                      <time dateTime={confirmedAt}>
                        {new Date(confirmedAt as string).toLocaleString(
                          language,
                          {
                            hour12: false,
                          },
                        )}
                      </time>
                    </span>
                    {knownSources.length > 0 && (
                      <span>
                        {t("上次确认来源：")}
                        {knownSources
                          .map((item) =>
                            t(
                              "{name} {messages} 条原文 · {conversations} 个会话",
                              {
                                name: item.display_name,
                                messages: number(item.imported_messages),
                                conversations: number(
                                  item.imported_conversations,
                                ),
                              },
                            ),
                          )
                          .join(t("；"))}
                      </span>
                    )}
                    {latest.length > 0 && (
                      <span>
                        {t("任务回执：")}
                        {latest
                          .map((item) =>
                            t(
                              "{name} {operation}，已扫描 {count} 条记录，{summary}",
                              {
                                name: nameOf(item.source_id),
                                operation: t(
                                  isSyncJob(item) ? "新增同步" : "历史导入",
                                ),
                                count: number(item.discovered),
                                summary: jobSummary(
                                  item,
                                  isSyncJob(item),
                                  language,
                                ),
                              },
                            ),
                          )
                          .join(t("；"))}
                      </span>
                    )}
                  </>
                ) : (
                  <span>{t("尚无已确认的来源状态，请刷新核对。")}</span>
                )}
                {refreshing && <span>{t("正在核对来源状态…")}</span>}
                {visibleError !== null && (
                  <span className="import-strip-refresh-error">
                    {t("核对失败，状态仍待核对。{detail}", {
                      detail: visibleError || t("请稍后重试。"),
                    })}
                  </span>
                )}
              </div>
            )}

            {!stale &&
              [runningHistory, runningSync].map((group, index) => {
                if (!group.length) return null;
                const sync = index === 1;
                const totals = group.reduce(
                  (sum, item) => ({
                    discovered: sum.discovered + item.discovered,
                    stored: sum.stored + item.stored,
                    merged: sum.merged + item.merged,
                    skipped: sum.skipped + item.skipped,
                    errors: sum.errors + item.errors,
                  }),
                  {
                    discovered: 0,
                    stored: 0,
                    merged: 0,
                    skipped: 0,
                    errors: 0,
                  },
                );
                return (
                  <span
                    className="import-strip-operation"
                    key={sync ? "sync" : "history"}
                  >
                    <span>
                      {t(
                        sync
                          ? "正在检查 {names} 的新增对话"
                          : "正在导入 {names} 的历史",
                        {
                          names: group
                            .map((item) => nameOf(item.source_id))
                            .join(t("、")),
                        },
                      )}
                      {totals.discovered > 0
                        ? t("，已扫描 {count} 条记录", {
                            count: number(totals.discovered),
                          })
                        : ""}
                      {t("。")}
                    </span>
                    <span>{jobSummary(totals, sync, language)}</span>
                  </span>
                );
              })}

            {!stale && !importing && backfilling && (
              <span>{t("正在准备已授权对话的整理任务。")}</span>
            )}

            {!stale &&
              pending.map((item) => (
                <span key={item.job_id}>
                  {t(
                    isSyncJob(item)
                      ? "{name} 新增同步等待检查"
                      : "{name} 历史等待导入",
                    { name: nameOf(item.source_id) },
                  )}
                  {" · "}
                  {jobSummary(item, isSyncJob(item), language)}
                </span>
              ))}

            {generating && (currentArrival?.backlogPending ?? 0) > 0 && (
              <span>{t("存量数据较大，为保证质量，正在后台分批整理")}</span>
            )}

            {failed.map((item) => (
              <span className="import-strip-failed" key={item.source_id}>
                {stale ? t("上次任务回执：") : ""}
                {t(
                  isSyncJob(item)
                    ? "{name} 新增同步失败"
                    : "{name} 没能读完历史",
                  { name: nameOf(item.source_id) },
                )}
                {t("。")}
                {!stale && jobSummary(item, isSyncJob(item), language)}
              </span>
            ))}

            {paused.map((item) => (
              <span className="import-strip-failed" key={item.source_id}>
                {stale ? t("上次任务回执：") : ""}
                {nameOf(item.source_id)}
                {t(
                  item.status === "cancelled"
                    ? " 已停止，"
                    : isSyncJob(item)
                      ? item.status === "paused"
                        ? " 新增同步已暂停，"
                        : " 新增同步已中断，"
                      : " 尚未导完历史，",
                )}
                {t(
                  stale
                    ? "请前往来源核对。"
                    : isSyncJob(item)
                      ? "可继续检查新增。"
                      : "可继续导入。",
                )}
                {!stale && jobSummary(item, isSyncJob(item), language)}
              </span>
            ))}

            {completedWithErrors.map((item) => (
              <span className="import-strip-failed" key={item.source_id}>
                {stale ? t("上次任务回执：") : ""}
                {t(
                  isSyncJob(item)
                    ? "{name} 本次新增检查已完成，仍有读取异常"
                    : "{name} 本次历史检查已完成，仍有读取异常",
                  { name: nameOf(item.source_id) },
                )}
                {stale
                  ? t("。")
                  : t("；{summary}。", {
                      summary: jobSummary(item, isSyncJob(item), language),
                    })}
              </span>
            ))}

            {!stale && knownSources.length > 0 && (
              <span className="import-strip-total">
                {t("累计已保存：")}
                {knownSources
                  .map((item) =>
                    t("{name} {messages} 条原文 · {conversations} 个会话", {
                      name: item.display_name,
                      messages: number(item.imported_messages),
                      conversations: number(item.imported_conversations),
                    }),
                  )
                  .join(t("；"))}
              </span>
            )}

            {(issues?.length ?? 0) > 0 && (
              <ul className="import-strip-receipts">
                {(issues ?? []).map((issue) => (
                  <li key={`${issue.job_id}:${issue.issue_id}`}>
                    {t(issue.kind === "error" ? "读取异常" : "跳过记录")}
                    {" · "}
                    {importIssueLabel(issue.reason, language)}
                  </li>
                ))}
              </ul>
            )}

            {(currentArrival?.blockedStages?.length ?? 0) > 0 && (
              <ul className="import-strip-receipts">
                {(currentArrival?.blockedStages ?? []).map((block) => (
                  <li key={`${block.kind}:${block.reason}`}>
                    {t("{kind} · {reason} · {count} 项", {
                      kind: taskKindLabel(block.kind, language),
                      reason: blockReasonLabel(block.reason, language),
                      count: number(block.count),
                    })}
                  </li>
                ))}
              </ul>
            )}

            {jobs.length > 0 && (
              <ul
                className="import-strip-receipts"
                aria-label={t("完整任务回执")}
              >
                {jobs.map((item) => (
                  <li key={item.job_id}>
                    {nameOf(item.source_id)} ·{" "}
                    {t(isSyncJob(item) ? "新增同步" : "历史导入")} ·{" "}
                    {jobStatusLabel(item.status, language)} ·{" "}
                    {jobSummary(item, isSyncJob(item), language)}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </details>
      )}

      {!stale && !jobFlight && jobFailure && (
        <p
          className="import-strip-refresh-error import-strip-job-error"
          role="alert"
        >
          {jobFailure.failures
            .map(({ job: failed, message, internal }) =>
              t("{name} 未能{action}：{message}", {
                name: nameOf(failed.source_id),
                action: t(jobFailure.action === "pause" ? "暂停" : "继续"),
                message: internal ? t(internal) : message,
              }),
            )
            .join(t("；"))}
        </p>
      )}
      <div className="import-strip-actions">
        {!stale && onJobAction && (jobFlight || jobAction) && (
          <button
            type="button"
            className="quiet import-strip-job"
            disabled={jobFlight !== null}
            onClick={() =>
              jobAction &&
              void changeJobs(
                jobAction,
                jobAction === "pause" ? pausable : resumable,
              )
            }
          >
            {jobFlight ? (
              <LoaderCircle size={16} aria-hidden="true" />
            ) : jobAction === "pause" ? (
              <Pause size={16} aria-hidden="true" />
            ) : (
              <Play size={16} aria-hidden="true" />
            )}
            {t(
              jobFlight === "pause"
                ? "暂停中…"
                : jobFlight === "resume"
                  ? "继续中…"
                  : jobAction === "pause"
                    ? "全部暂停"
                    : "全部继续",
            )}
          </button>
        )}
        {!stale && !jobFlight && jobFailure && retryTargets.length > 0 && (
          <button
            type="button"
            className="quiet import-strip-job"
            onClick={() => void changeJobs(jobFailure.action, retryTargets)}
          >
            <RefreshCw size={16} aria-hidden="true" />
            {t(jobFailure.action === "pause" ? "重试暂停" : "重试继续")}
          </button>
        )}
        {!stale &&
          currentArrival?.phase === "model-required" &&
          onConfigureModel && (
            <button type="button" className="quiet" onClick={onConfigureModel}>
              <Settings2 size={16} aria-hidden="true" />
              {t("设置模型")}
            </button>
          )}
        {!stale &&
          currentArrival &&
          ["failed", "stopped"].includes(currentArrival.phase) &&
          onConfigureModel && (
            <button type="button" className="quiet" onClick={onConfigureModel}>
              <Settings2 size={16} aria-hidden="true" />
              {t("继续整理")}
            </button>
          )}
        {stale && onRefresh && (
          <button
            type="button"
            className="quiet import-strip-refresh"
            title={t("刷新核对")}
            disabled={refreshing}
            onClick={() => void refresh()}
          >
            <RefreshCw size={16} aria-hidden="true" />
            {t("刷新核对")}
          </button>
        )}
        {showSourceLink && (
          <button type="button" className="quiet" onClick={onOpenSources}>
            {t("查看来源")}
          </button>
        )}
      </div>
    </div>
  );
}

/** A late update to an old resumed job does not make it the newest source job. */
function latestBySource(jobs: ImportJob[]): ImportJob[] {
  const newest = new Map<string, ImportJob>();
  for (const item of jobs) {
    const held = newest.get(item.source_id);
    if (
      !held ||
      timestamp(item.created_at) > timestamp(held.created_at) ||
      (timestamp(item.created_at) === timestamp(held.created_at) &&
        timestamp(item.updated_at) >= timestamp(held.updated_at))
    )
      newest.set(item.source_id, item);
  }
  return [...newest.values()];
}

function timestamp(value: string) {
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY;
}

function isSyncJob(job: ImportJob) {
  return job.operation === "sync";
}

/** What a job or a group of jobs actually saved, in the reader's own language.
 *
 * A sync that found nothing new and a history import that saved nothing are
 * different sentences, so the caller says which one it is. */
function jobSummary(
  totals: {
    stored: number;
    merged: number;
    skipped: number;
    errors: number;
  },
  sync = false,
  language: UiLanguage = "zh-CN",
) {
  const count = (value: number) => formatUiNumber(language, value);
  return `${translate(language, sync ? "本次新增 {count} 条原文" : "{count} 条原文已保存", { count: count(totals.stored) })}${totals.merged > 0 ? translate(language, " · {count} 条已存在", { count: count(totals.merged) }) : ""}${totals.skipped > 0 ? translate(language, " · {count} 条跳过", { count: count(totals.skipped) }) : ""}${totals.errors > 0 ? translate(language, " · {count} 条异常", { count: count(totals.errors) }) : ""}`;
}
