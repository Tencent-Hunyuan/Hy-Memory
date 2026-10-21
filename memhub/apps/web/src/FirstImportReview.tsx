import type { ImportJob, SourceResponse } from "@one-memory/contracts";
import { Pause, Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { connectionHostId } from "./connection-hosts";
import { R6ColdStartFrame } from "./R6ColdStartFrame";
import { R6Button } from "./R6Controls";
import { R6ImportProgress } from "./R6ImportProgress";
import { useTranslate } from "./ui-language";
import "./first-import-r6.css";

export function importHostEligible(
  sources: readonly SourceResponse[],
  sourceIds: readonly string[],
) {
  const ids = new Set(sourceIds);
  return sources.filter(
    (source) =>
      ids.has(source.source_id) &&
      !source.source_id.startsWith("deepseek-harness") &&
      source.source_id !== "openclaw" &&
      (connectionHostId(source.source_id) !== null ||
        (source.capabilities.installer === "implemented" &&
          source.capabilities.recall === "implemented")),
  );
}

export function defaultHostSourceIds(
  sources: readonly SourceResponse[],
  sourceIds: readonly string[],
) {
  const eligible = importHostEligible(sources, sourceIds).map(
    (source) => source.source_id,
  );
  const known = new Set(eligible);
  for (const id of sourceIds) {
    if (known.has(id) || id.startsWith("deepseek-harness") || id === "openclaw")
      continue;
    if (connectionHostId(id) !== null) {
      eligible.push(id);
      known.add(id);
    }
  }
  return eligible;
}

export type ImportConnectionDraft = {
  hostSourceIds: readonly string[];
};
export interface FirstImportReviewProps {
  sourceIds: readonly string[];
  sources: SourceResponse[];
  /** Only this accepted history batch; sync jobs are never actionable here. */
  jobs: ImportJob[];
  acceptedJobIds?: readonly string[];
  failedSources?: Record<string, string>;
  sourceSnapshot?: { confirmedAt: string | null; stale: boolean };
  busy?: boolean;
  error?: string | null;
  connectionNote?: string;
  recovery?: { destination: "memory" | "settings" | "sources"; onSkip(): void };
  onChangeJob?(
    jobId: string,
    action: "pause" | "resume" | "cancel",
  ): Promise<ImportJob>;
  connectionDraft?: ImportConnectionDraft;
  onConnectionDraftChange?(draft: ImportConnectionDraft): void;
  /** True only when the parent has connected step3's explicit execution/receipts. */
  connectionExecutionAvailable?: boolean;
  hostConnectionAvailable?: boolean;
  /** Parent routes settings to step3. This component never completes onboarding. */
  onContinue(destination: "memory" | "settings" | "sources"): void;
}

export function FirstImportReview({
  sourceIds,
  sources,
  jobs,
  acceptedJobIds,
  failedSources = {},
  sourceSnapshot,
  busy = false,
  error,
  connectionNote,
  hostConnectionAvailable = true,
  recovery,
  onChangeJob,
  connectionDraft,
  onConnectionDraftChange,
  onContinue,
}: FirstImportReviewProps) {
  const t = useTranslate();
  const stale = sourceSnapshot?.stale === true;
  const ids = [...new Set(sourceIds)];
  // Without explicit IDs, capture the initial batch once; later sync/history jobs
  // must not become targets just because their source matches.
  const initialJobs = useRef(
    jobs
      .filter(
        (job) => job.operation !== "sync" && sourceIds.includes(job.source_id),
      )
      .map((job) => job.job_id),
  );
  const batchIds = acceptedJobIds ?? initialJobs.current;
  const [receipts, setReceipts] = useState<Record<string, ImportJob>>({});
  const batch = jobs
    .filter(
      (job) =>
        batchIds.includes(job.job_id) &&
        ids.includes(job.source_id) &&
        job.operation !== "sync",
    )
    .map((job) => {
      const saved = receipts[job.job_id];
      return saved &&
        sameJob(saved, job) &&
        Date.parse(saved.updated_at) >= Date.parse(job.updated_at)
        ? saved
        : job;
    });
  const historyWindowLimited = batch.some(
    (job) => typeof job.history_after === "string",
  );
  const progressStale = stale && batch.length === 0;
  const [changing, setChanging] = useState(false);
  const [actionMessage, setActionMessage] = useState("");
  const batchFlight = useRef(false),
    stopBatch = useRef(false),
    mounted = useRef(true);
  const live = useRef({ batch, sources, stale, busy });
  live.current = { batch, sources, stale, busy };
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      stopBatch.current = true;
    };
  }, []);
  const canPause = batch.some((job) =>
    ["running", "pending"].includes(job.status),
  );
  const canResume = batch.some((job) =>
    ["paused", "interrupted", "failed"].includes(job.status),
  );
  const allCompleted =
    batch.length > 0 &&
    ids.every((id) => batch.some((job) => job.source_id === id)) &&
    batch.every((job) => job.status === "completed") &&
    !Object.keys(failedSources).length;
  const allPaused =
    batch.length > 0 && batch.every((job) => job.status === "paused");
  const importing =
    !stale &&
    batch.some(
      (job) =>
        ["pending", "running"].includes(job.status) &&
        sources.some(
          (source) => source.source_id === job.source_id && source.authorized,
        ),
    );
  const displayImporting =
    importing ||
    (!stale &&
      !allCompleted &&
      !allPaused &&
      batch.length === 0 &&
      ids.some((id) =>
        sources.some((source) => source.source_id === id && source.authorized),
      ));
  const title = t("对话正在汇入，记忆从这里开始");
  const hostEligible = importHostEligible(sources, ids);
  const draftReady = !!connectionDraft && !!onConnectionDraftChange;

  function leave(destination: "memory" | "settings" | "sources") {
    stopBatch.current = true;
    onContinue(destination);
  }
  async function changeBatch(action: "pause" | "resume") {
    if (
      batchFlight.current ||
      !onChangeJob ||
      busy ||
      (stale && action === "resume")
    )
      return;
    const targets = batch
      .filter((job) =>
        action === "pause"
          ? ["running", "pending"].includes(job.status)
          : ["paused", "interrupted", "failed"].includes(job.status),
      )
      .map((job) => ({
        job,
        sourceKey: sourceKey(
          sources.find((source) => source.source_id === job.source_id),
        ),
      }));
    if (!targets.length) return;
    batchFlight.current = true;
    stopBatch.current = false;
    setChanging(true);
    setActionMessage("");
    let confirmed = 0,
      failed = 0;
    try {
      for (const target of targets) {
        const current = live.current;
        const job = current.batch.find((job) => sameJob(job, target.job));
        const source = current.sources.find(
          (source) => source.source_id === target.job.source_id,
        );
        if (
          stopBatch.current ||
          !mounted.current ||
          (current.stale && action === "resume") ||
          current.busy
        )
          break;
        if (
          !job ||
          !source?.authorized ||
          sourceKey(source) !== target.sourceKey
        ) {
          failed++;
          continue;
        }
        if (
          action === "resume" &&
          !["paused", "interrupted", "failed"].includes(job.status)
        )
          continue;
        if (action === "pause" && !["pending", "running"].includes(job.status))
          continue;
        try {
          const saved = await onChangeJob(job.job_id, action);
          if (!mounted.current) return;
          const sourceNow = live.current.sources.find(
            (source) => source.source_id === job.source_id,
          );
          if (
            (live.current.stale && action === "resume") ||
            !sourceNow?.authorized ||
            sourceKey(sourceNow) !== target.sourceKey ||
            !live.current.batch.some((value) => sameJob(value, job))
          ) {
            stopBatch.current = true;
            failed++;
            continue;
          }
          if (
            !sameJob(saved, job) ||
            Date.parse(saved.updated_at) < Date.parse(job.updated_at) ||
            !Number.isFinite(Date.parse(saved.updated_at)) ||
            !(action === "pause"
              ? ["paused", "completed", "cancelled", "failed"].includes(
                  saved.status,
                )
              : ["pending", "running", "completed"].includes(saved.status))
          )
            throw Error("invalid_receipt");
          setReceipts((previous) => ({ ...previous, [saved.job_id]: saved }));
          confirmed++;
        } catch {
          failed++;
        }
      }
      if (mounted.current)
        setActionMessage(
          `${stopBatch.current ? t("已停止后续操作；已发出的操作仍按回执核对。") : ""}${t("已确认 {confirmed} 项，{failed} 项未确认。", { confirmed, failed })}${failed ? t("已确认项保留，请核对未确认任务后重试。") : ""}`,
        );
    } finally {
      batchFlight.current = false;
      if (mounted.current) setChanging(false);
    }
  }

  return (
    <R6ColdStartFrame
      step={2}
      title={title}
      eyebrow={t("导入进行中 · 顺手连接应用")}
      footer={
        <>
          <div className="first-import-r6-footer-note">
            <p>
              {allCompleted
                ? t("对话已保存，记忆尚待生成。")
                : displayImporting
                  ? t("导入在后台继续，无需等待。")
                  : stale
                    ? t("正在重新核对来源；已保存数量仍按任务回执显示。")
                    : allPaused
                      ? t("本次历史导入已暂停，已保存的对话还在。")
                      : t("导入等待处理，已保存的对话还在。")}
            </p>
            {(error || actionMessage) && (
              <p
                role={
                  error?.includes("应用连接") ||
                  error?.includes("成功设置已保留")
                    ? "status"
                    : error
                      ? "alert"
                      : "status"
                }
                className={
                  error?.includes("应用连接") ||
                  error?.includes("成功设置已保留")
                    ? "first-import-r6-partial-success"
                    : undefined
                }
              >
                {error || actionMessage}
              </p>
            )}
          </div>
          <div className="first-import-r6-footer-actions">
            {changing ? (
              <R6Button
                onClick={() => {
                  stopBatch.current = true;
                }}
              >
                {t("停止后续操作")}
              </R6Button>
            ) : onChangeJob && (canPause || canResume) ? (
              <R6Button
                disabled={busy || (stale && !canPause)}
                onClick={() => void changeBatch(canPause ? "pause" : "resume")}
              >
                {canPause ? (
                  <Pause size={16} aria-hidden="true" />
                ) : (
                  <Play size={16} aria-hidden="true" />
                )}
                {canPause ? t("暂停导入") : t("继续导入")}
              </R6Button>
            ) : null}
            {recovery ? (
              <>
                <R6Button
                  disabled={busy}
                  onClick={() => {
                    stopBatch.current = true;
                    recovery.onSkip();
                  }}
                >
                  {recovery.destination === "sources"
                    ? t("仍进入应用页")
                    : recovery.destination === "settings"
                      ? t("仍进入设置")
                      : t("仍进入工作台")}
                </R6Button>
                <R6Button
                  variant="primary"
                  disabled={busy}
                  onClick={() => leave(recovery.destination)}
                >
                  {t("重试保存并进入")}
                </R6Button>
              </>
            ) : (
              <R6Button
                variant="primary"
                disabled={busy}
                onClick={() => leave("settings")}
              >
                {t("配置模型，沉淀记忆")}
              </R6Button>
            )}
          </div>
        </>
      }
    >
      <div className="first-import-r6-layout">
        <section className="first-import-r6-main" aria-label={t("导入与原文")}>
          <p className="first-import-r6-intro">
            {allCompleted
              ? t("本次导入已结束。已保存的对话无需重复导入。")
              : t("对话先保存在本机。你可以同时设置后续连接。")}
          </p>
          <p className="first-import-r6-subintro">
            {t("导入不会自动开放应用读取权限。配置模型后，才会开始生成记忆。")}
          </p>
          {historyWindowLimited && (
            <p className="first-import-r6-history-window" role="status">
              {t(
                "检测到会话数据量较大。为控制用量并保持各应用时间线一致，本次所有应用仅导入最近 3 天的对话。",
              )}
            </p>
          )}
          <R6ImportProgress
            sourceIds={ids}
            sources={sources}
            jobs={batch}
            stale={progressStale}
          />
          <section className="first-import-r6-feed" aria-label={t("导入说明")}>
            <h2>{t("正在导入你的会话")}</h2>
            <p className="first-import-r6-feed-status">
              {t("后台将持续为你导入，可以放心进入下一步")}
            </p>
            {Object.keys(failedSources).length > 0 && (
              <section
                className="first-import-r6-failures"
                aria-label={t("未开始的导入")}
              >
                {Object.keys(failedSources).map((id) => (
                  <p key={id}>
                    {t("{name} 未能开始导入，其他已接受任务不受影响。", {
                      name:
                        sources.find((source) => source.source_id === id)
                          ?.display_name ?? id,
                    })}
                  </p>
                ))}
                <R6Button
                  variant="quiet"
                  disabled={busy}
                  onClick={() => leave("sources")}
                >
                  {t("查看未导入的应用")}
                </R6Button>
              </section>
            )}
          </section>
        </section>
        <aside className="first-import-r6-options" aria-label={t("连接应用")}>
          <p className="first-import-r6-eyebrow">{t("让记忆继续生长")}</p>
          <h2>{t("连接你的应用")}</h2>
          <p className="first-import-r6-options-intro">
            {t("适用于本次选择且支持的应用。")}
          </p>
          <SelectionTile
            title={
              hostConnectionAvailable
                ? t("一键接入应用")
                : t("测试实例不接入应用")
            }
            body={
              hostConnectionAvailable
                ? t("安装记忆连接，让所选应用用上 MemHub 中的相关记忆。")
                : t("当前使用独立测试数据目录，不会修改本机应用的连接配置。")
            }
            checked={
              hostConnectionAvailable &&
              !!hostEligible.length &&
              hostEligible.every((source) =>
                connectionDraft?.hostSourceIds.includes(source.source_id),
              )
            }
            disabled={
              !draftReady ||
              busy ||
              !hostEligible.length ||
              !hostConnectionAvailable
            }
            onChange={(checked) =>
              onConnectionDraftChange?.({
                hostSourceIds: checked
                  ? hostEligible.map((source) => source.source_id)
                  : [],
              })
            }
          />
          {connectionNote && (
            <p className="first-import-r6-options-note">{connectionNote}</p>
          )}
          <p className="first-import-r6-options-note">
            {draftReady
              ? t(
                  "点击底部按钮后，后台分别接入各应用；某个应用失败不影响其他应用。",
                )
              : t("暂时无法连接应用，当前不会安装连接。")}
          </p>
        </aside>
      </div>
    </R6ColdStartFrame>
  );
}

function SelectionTile({
  title,
  body,
  checked,
  disabled,
  onChange,
}: {
  title: string;
  body: string;
  checked: boolean;
  disabled: boolean;
  onChange(checked: boolean): void;
}) {
  return (
    <label
      className="first-import-r6-selection"
      data-checked={checked}
      aria-disabled={disabled}
    >
      <strong>{title}</strong>
      <input
        type="checkbox"
        aria-label={title}
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span>{body}</span>
    </label>
  );
}

function sourceKey(source: SourceResponse | undefined) {
  return JSON.stringify(
    source
      ? [
          source.source_id,
          source.authorized,
          source.grant_revision,
          source.root_id,
          source.root_label,
          source.candidate_path,
          source.candidate_root_id,
          source.candidate_revision,
        ]
      : null,
  );
}
function sameJob(a: ImportJob, b: ImportJob) {
  return (
    a.job_id === b.job_id &&
    a.source_id === b.source_id &&
    a.user_id === b.user_id &&
    a.created_at === b.created_at &&
    (a.operation ?? "history") === (b.operation ?? "history")
  );
}
