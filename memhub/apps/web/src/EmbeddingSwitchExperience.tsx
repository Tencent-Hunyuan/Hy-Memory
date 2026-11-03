import type {
  EmbeddingSwitchCurrent,
  EmbeddingSwitchJob,
  EmbeddingSwitchPlan,
} from "@one-memory/contracts";
import { ChevronDown, CircleAlert, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { deleteRetiredEmbeddingSpace } from "./api";
import { R6Button } from "./R6Controls";
import { useEmbeddingSwitch } from "./use-embedding-switch";
import "./embedding-switch.css";

function count(value: number | null | undefined) {
  return new Intl.NumberFormat().format(value ?? 0);
}

function duration(seconds: number | null) {
  if (seconds === null) return "暂时无法估算";
  if (seconds < 60) return `约 ${Math.max(1, Math.round(seconds))} 秒`;
  return `约 ${Math.max(1, Math.round(seconds / 60))} 分钟`;
}

function size(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`;
  return `${Math.ceil(bytes / 1024 / 1024)} MB`;
}

const blockerCopy: Record<string, string> = {
  embedding_probe_required: "请先测试新的搜索模型连接。",
  embedding_dimensions_unknown:
    "还无法确认新搜索模型的索引规格，请重新测试连接。",
  embedding_switch_requires_migration:
    "旧版本数据仍在升级，请等待升级完成后再更换。",
  sdk_reembed_unavailable: "当前安装版本还不能安全更新记忆索引。",
  insufficient_disk_space: "本机剩余空间不足，无法安全建立新索引。",
  embedding_switch_running: "另一次索引更新正在进行。",
  memory_lifecycle_busy: "记忆正在整合，请等待完成后再更换搜索模型。",
};

const failureCopy: Record<string, string> = {
  writes_did_not_drain: "等待正在进行的记忆整理结束超时。",
  embedding_service_failed: "搜索模型服务连续请求失败。",
  embedding_dimensions_mismatch: "搜索服务返回的索引规格与测试结果不一致。",
  embedding_verification_failed: "新的记忆索引没有通过完整性校验。",
  target_profile_changed: "更新期间搜索模型配置发生了变化。",
  embedding_switch_cancelled: "索引更新已取消。",
  model_secret_unavailable: "无法读取搜索服务的 API Key。",
  secure_storage_unavailable: "系统安全存储暂时不可用。",
  embedding_switch_clients_busy: "记忆服务一直被占用，暂时无法完成切换。",
};

export function EmbeddingSwitchConfirmDialog({
  plan,
  busy,
  errorCode,
  onCancel,
  onStart,
}: {
  plan: EmbeddingSwitchPlan;
  busy: boolean;
  errorCode: string | null;
  onCancel(): void;
  onStart(): void;
}) {
  const panel = useRef<HTMLElement>(null);
  const blockers = plan.blockers.map(
    (code) => blockerCopy[code] ?? "当前状态暂时不允许更换搜索模型。",
  );

  useEffect(() => {
    const opener =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    panel.current
      ?.querySelector<HTMLButtonElement>("button:not(:disabled)")
      ?.focus();
    return () => {
      if (opener?.isConnected) opener.focus();
    };
  }, []);

  return (
    <div
      className="r6-settings-dialog-layer embedding-switch-dialog-layer"
      role="presentation"
    >
      <section
        ref={panel}
        className="r6-settings-dialog embedding-switch-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="embedding-switch-title"
        onKeyDown={(event) => {
          if (event.key === "Escape" && !busy) onCancel();
          if (event.key !== "Tab" || !panel.current) return;
          const controls = [
            ...panel.current.querySelectorAll<HTMLElement>(
              "button:not(:disabled), summary, input:not(:disabled), select:not(:disabled)",
            ),
          ];
          const first = controls[0];
          const last = controls.at(-1);
          if (!first || !last) return;
          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
          }
        }}
      >
        <header>
          <div>
            <h2 id="embedding-switch-title">更新记忆搜索索引？</h2>
            <p>
              不同搜索模型建立的索引不能混用。为了继续找到已有记忆，需要重新建立索引。
            </p>
          </div>
        </header>

        <dl className="embedding-switch-impact">
          <div>
            <dt>已有记忆</dt>
            <dd>
              {count(plan.total_vectors)} 条
              {plan.users && plan.users > 1
                ? ` · ${count(plan.users)} 位用户`
                : ""}
            </dd>
          </div>
          <div>
            <dt>预计耗时</dt>
            <dd>{duration(plan.estimate_s)}</dd>
          </div>
          <div>
            <dt>远程请求</dt>
            <dd>
              {plan.target_local
                ? "不会产生"
                : plan.requests === null
                  ? "按服务商计费"
                  : `约 ${count(plan.requests)} 次`}
            </dd>
          </div>
        </dl>

        <div className="embedding-switch-safety">
          <strong>更新期间仍可搜索记忆</strong>
          <p>
            MemHub
            会继续使用原来的搜索模型。新对话仍会保存，更新完成后再整理成记忆。
          </p>
          <strong>失败不会影响已有数据</strong>
          <p>
            只有全部更新并校验通过后才会切换；失败或取消时仍使用原来的模型。
          </p>
        </div>

        <details className="embedding-switch-details">
          <summary>
            <ChevronDown size={14} aria-hidden="true" />
            查看技术详情
          </summary>
          <dl>
            <div>
              <dt>当前搜索模型</dt>
              <dd>{plan.source.model ?? "旧版本索引"}</dd>
            </div>
            <div>
              <dt>新的搜索模型</dt>
              <dd>{plan.target.model ?? "待确认"}</dd>
            </div>
            <div>
              <dt>索引维度</dt>
              <dd>
                {plan.source.dims ?? "未知"} → {plan.target.dims ?? "未知"}
              </dd>
            </div>
            <div>
              <dt>额外磁盘空间</dt>
              <dd>{size(plan.disk_bytes_needed)}</dd>
            </div>
            <div>
              <dt>旧索引保留</dt>
              <dd>{plan.retention_days} 天</dd>
            </div>
          </dl>
        </details>

        {blockers.length > 0 ? (
          <div className="embedding-switch-alert" role="alert">
            <CircleAlert size={16} aria-hidden="true" />
            <span>{blockers.join(" ")}</span>
          </div>
        ) : null}
        {errorCode ? (
          <div className="embedding-switch-alert" role="alert">
            <CircleAlert size={16} aria-hidden="true" />
            <span>
              {errorCode === "embedding_switch_plan_stale"
                ? "记忆状态发生了变化，请重新检查更换影响。"
                : "索引更新暂时无法开始，请稍后重试。"}
            </span>
          </div>
        ) : null}

        <footer>
          <R6Button disabled={busy} onClick={onCancel}>
            暂不更换
          </R6Button>
          <R6Button
            variant="primary"
            pending={busy}
            disabled={blockers.length > 0}
            onClick={onStart}
          >
            开始更新索引
          </R6Button>
        </footer>
      </section>
    </div>
  );
}

function jobTitle(job: EmbeddingSwitchJob) {
  if (job.kind === "merge") return "正在整理旧版本留下的搜索索引";
  if (job.state === "preparing") return "正在准备更新记忆索引";
  if (job.state === "reembedding") return "正在更新记忆索引";
  if (job.state === "verifying") return "正在校验新的记忆索引";
  if (job.state === "committing") return "正在启用新的搜索模型";
  if (job.state === "failed") return "更新没有完成，正在恢复原设置";
  return "记忆索引更新";
}

function canCancel(job: EmbeddingSwitchJob) {
  return ["preparing", "reembedding", "verifying"].includes(job.state);
}

function ActiveSwitchBanner({
  current,
  onCancel,
  cancelling,
}: {
  current: EmbeddingSwitchCurrent;
  onCancel(): void;
  cancelling: boolean;
}) {
  const job = current.job;
  if (!job && current.phase !== "scanning") return null;
  const percent = job?.progress.percent ?? 0;
  return (
    <aside className="embedding-switch-strip" aria-live="polite">
      <div className="embedding-switch-strip-copy">
        <strong>{job ? jobTitle(job) : "正在检查本机的记忆搜索索引"}</strong>
        <span>
          {job?.state === "failed"
            ? "原来的搜索模型仍在使用，记忆没有丢失。"
            : job
              ? `${Math.round(percent)}%${
                  job.progress.eta_s === null
                    ? ""
                    : ` · ${duration(job.progress.eta_s)}`
                }`
              : "完成前暂不整理新记忆，搜索仍可使用。"}
        </span>
      </div>
      {job ? (
        <div
          className="embedding-switch-progress"
          role="progressbar"
          aria-label="记忆索引更新进度"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(percent)}
        >
          <span style={{ width: `${Math.max(2, percent)}%` }} />
        </div>
      ) : null}
      {job && canCancel(job) ? (
        <R6Button variant="quiet" pending={cancelling} onClick={onCancel}>
          取消更新
        </R6Button>
      ) : null}
    </aside>
  );
}

export function EmbeddingSwitchGlobalStatus({
  onConfigureSearch,
  onDiagnostics,
}: {
  onConfigureSearch(): void;
  onDiagnostics(): void;
}) {
  const controller = useEmbeddingSwitch();
  const [recent, setRecent] = useState<EmbeddingSwitchJob | null>(null);
  const [cleaning, setCleaning] = useState(false);
  const [cleanupError, setCleanupError] = useState(false);
  const previousJob = useRef<string | null>(null);
  const alive = useRef(true);

  useEffect(
    () => () => {
      alive.current = false;
    },
    [],
  );

  useEffect(() => {
    const open = controller.current?.job?.job_id ?? null;
    if (
      previousJob.current &&
      !open &&
      controller.current?.last_finished?.job_id === previousJob.current
    ) {
      setRecent(controller.current.last_finished);
    }
    previousJob.current = open;
  }, [controller.current]);

  return (
    <>
      {controller.current ? (
        <ActiveSwitchBanner
          current={controller.current}
          cancelling={controller.busy === "cancel"}
          onCancel={() => void controller.cancelCurrentSwitch()}
        />
      ) : null}
      {controller.refreshFailed && controller.supported === true ? (
        <aside className="embedding-switch-outcome is-error" role="status">
          <div>
            <strong>索引状态暂时无法读取</strong>
            <span>当前显示可能不是最新状态，可以继续使用已有记忆。</span>
          </div>
          <R6Button variant="quiet" onClick={() => void controller.refresh()}>
            重新读取
          </R6Button>
        </aside>
      ) : null}
      {controller.current &&
      ["failed", "unavailable"].includes(controller.current.scan_state) ? (
        <aside className="embedding-switch-outcome is-error" role="alert">
          <div>
            <strong>旧版本搜索索引检查未完成</strong>
            <span>
              {controller.current.writes_paused
                ? "新的记忆整理暂时暂停，已有记忆仍可搜索。"
                : "记忆整理已经恢复；可以继续使用，诊断中保留了失败原因。"}
            </span>
          </div>
          <R6Button variant="quiet" onClick={onDiagnostics}>
            查看诊断
          </R6Button>
        </aside>
      ) : null}
      {recent?.state === "done" ? (
        <aside
          className="embedding-switch-outcome"
          role="status"
          aria-live="polite"
        >
          <div>
            <strong>搜索模型已更换</strong>
            <span>暂停期间保存的新对话正在整理成记忆。</span>
          </div>
          <div className="embedding-switch-outcome-actions">
            {recent.source.space_id ? (
              <R6Button
                variant="quiet"
                pending={cleaning}
                onClick={() => {
                  if (!recent.source.space_id) return;
                  setCleaning(true);
                  setCleanupError(false);
                  void deleteRetiredEmbeddingSpace(recent.source.space_id)
                    .then(() => {
                      if (alive.current) setRecent(null);
                    })
                    .catch(() => {
                      if (alive.current) setCleanupError(true);
                    })
                    .finally(() => {
                      if (alive.current) setCleaning(false);
                    });
                }}
              >
                清理旧索引
              </R6Button>
            ) : null}
            <R6Button
              variant="quiet"
              aria-label="关闭完成提示"
              onClick={() => setRecent(null)}
            >
              <X size={16} aria-hidden="true" />
            </R6Button>
          </div>
        </aside>
      ) : null}
      {cleanupError ? (
        <p className="embedding-switch-cleanup-error" role="alert">
          旧索引暂时无法清理，MemHub 会在保留期结束后自动重试。
        </p>
      ) : null}
      {recent?.state === "rolled_back" ? (
        <aside
          className="embedding-switch-outcome is-error"
          role="alert"
          aria-live="assertive"
        >
          <div>
            <strong>索引更新没有完成</strong>
            <span>
              {failureCopy[recent.error_code ?? ""]
                ? `${failureCopy[recent.error_code ?? ""]} 仍在使用原来的搜索模型，记忆没有丢失。`
                : "仍在使用原来的搜索模型，记忆没有丢失。"}
            </span>
          </div>
          <div className="embedding-switch-outcome-actions">
            <R6Button variant="quiet" onClick={onDiagnostics}>
              查看诊断
            </R6Button>
            <R6Button variant="quiet" onClick={onConfigureSearch}>
              检查连接
            </R6Button>
            <R6Button
              variant="quiet"
              aria-label="关闭失败提示"
              onClick={() => setRecent(null)}
            >
              <X size={16} aria-hidden="true" />
            </R6Button>
          </div>
        </aside>
      ) : null}
    </>
  );
}
