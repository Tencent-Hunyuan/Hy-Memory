import type { ImportJob, SourceResponse } from "@one-memory/contracts";
import { useEffect, useLayoutEffect, useRef } from "react";
import { AppMark } from "./app-mark";
import "./r6-import-progress.css";

function progressText(job: ImportJob): string | null {
  if (job.stored > 0) return `已保存 ${job.stored.toLocaleString()} 条消息`;
  if (job.discovered <= 0) return null;
  const scanned = `已扫描 ${job.discovered.toLocaleString()} 条`;
  if (job.status === "completed")
    return job.history_after
      ? `${scanned}，本次没有符合时间范围的对话`
      : `${scanned}，本次没有可保存的对话`;
  if (job.status === "pending" || job.status === "running")
    return job.history_after
      ? `${scanned}，正在筛选最近 3 天`
      : `${scanned}，正在筛选`;
  return `${scanned}，尚未保存`;
}

export function R6ImportProgress({
  sourceIds,
  sources,
  jobs,
  stale = false,
}: {
  sourceIds: readonly string[];
  sources: SourceResponse[];
  jobs: ImportJob[];
  stale?: boolean;
}) {
  const scroller = useRef<HTMLElement>(null);
  const userScrollLeft = useRef(0);
  const userScrollIntent = useRef(false);
  const intentTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  function markUserScrollIntent() {
    userScrollIntent.current = true;
    if (intentTimer.current) clearTimeout(intentTimer.current);
    intentTimer.current = setTimeout(() => {
      userScrollIntent.current = false;
    }, 500);
  }

  useLayoutEffect(() => {
    const element = scroller.current;
    if (!element || userScrollIntent.current) return;
    if (element.scrollLeft !== userScrollLeft.current)
      element.scrollLeft = userScrollLeft.current;
  });
  useEffect(() => () => {
    if (intentTimer.current) clearTimeout(intentTimer.current);
  });

  const ids = [...new Set(sourceIds)];
  return (
    <section
      className="r6-import-progress"
      aria-label="本次应用导入进度"
      data-count={ids.length <= 2 ? String(ids.length) : "many"}
      ref={scroller}
      onPointerDown={markUserScrollIntent}
      onTouchStart={markUserScrollIntent}
      onWheel={markUserScrollIntent}
      onKeyDown={(event) => {
        if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))
          markUserScrollIntent();
      }}
      onScroll={(event) => {
        if (userScrollIntent.current)
          userScrollLeft.current = event.currentTarget.scrollLeft;
      }}
    >
      {ids.map((id) => {
        const source = sources.find((item) => item.source_id === id);
        const job = jobs
          .filter((item) => item.source_id === id)
          .reduce<ImportJob | undefined>(
            (latest, item) =>
              !latest ||
              Date.parse(item.created_at) > Date.parse(latest.created_at) ||
              (item.job_id === latest.job_id &&
                Date.parse(item.updated_at) > Date.parse(latest.updated_at))
                ? item
                : latest,
            undefined,
          );
        const running =
          !stale &&
          source?.authorized === true &&
          (!job || ["pending", "running"].includes(job.status));
        const noImportedHistory =
          job &&
          ["cancelled", "completed", "interrupted"].includes(job.status) &&
          job.stored === 0 &&
          job.discovered === 0;
        const names: Record<ImportJob["status"], string> = {
          pending: "正在导入",
          running: "正在导入",
          paused: "已暂停",
          interrupted: "已中断",
          failed: "导入遇到问题",
          cancelled: "已取消",
          completed: "导入完成",
        };
        const progress = job ? progressText(job) : null;
        return (
          <article
            className="r6-import-progress-card"
            key={id}
            aria-label={`${source?.display_name ?? id} 导入进度`}
            data-running={running ? "true" : "false"}
          >
            <div className="r6-import-progress-orbit" aria-hidden="true">
              <span />
              <div className="r6-import-progress-icon">
                <AppMark id={id} label={source?.display_name ?? id} size={30} />
              </div>
            </div>
            <h2>{source?.display_name ?? id}</h2>
            {progress ? (
              <p>{progress}</p>
            ) : noImportedHistory ? (
              <p>没有可保存的历史对话</p>
            ) : null}
            <small role="status">
              {stale
                ? "状态待核对"
                : source?.authorized !== true
                  ? "读取授权不可用"
                  : job
                    ? names[job.status]
                    : "正在导入"}
              {job && job.errors > 0
                ? ` · ${job.errors.toLocaleString()} 条读取异常`
                : ""}
            </small>
          </article>
        );
      })}
    </section>
  );
}
