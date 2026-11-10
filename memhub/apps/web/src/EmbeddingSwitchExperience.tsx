import type {
  EmbeddingSwitchCurrent,
  EmbeddingSwitchJob,
  EmbeddingSwitchPlan,
} from "@one-memory/contracts";
import type { GuiLocale, MessageKey } from "@one-memory/i18n";
import { ChevronDown, CircleAlert, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { deleteRetiredEmbeddingSpace } from "./api";
import { useGuiLanguage } from "./gui-language";
import { R6Button } from "./R6Controls";
import { useEmbeddingSwitch } from "./use-embedding-switch";
import "./embedding-switch.css";

type Translator = (
  key: MessageKey,
  params?: Record<string, string | number>,
) => string;

function count(locale: GuiLocale, value: number | null | undefined) {
  return new Intl.NumberFormat(locale).format(value ?? 0);
}

function duration(locale: GuiLocale, t: Translator, seconds: number | null) {
  if (seconds === null) return t("embeddingSwitch.duration.unknown");
  const value = Math.max(1, Math.round(seconds < 60 ? seconds : seconds / 60));
  return t(
    seconds < 60
      ? "embeddingSwitch.duration.seconds"
      : "embeddingSwitch.duration.minutes",
    { count: count(locale, value) },
  );
}

function size(locale: GuiLocale, bytes: number) {
  const megabytes = bytes >= 1024 * 1024;
  const value = Math.ceil(bytes / 1024 / (megabytes ? 1024 : 1));
  return `${count(locale, value)} ${megabytes ? "MB" : "KB"}`;
}

const blockerKeys: Record<string, MessageKey> = {
  embedding_probe_required: "embeddingSwitch.blocker.embedding_probe_required",
  embedding_dimensions_unknown:
    "embeddingSwitch.blocker.embedding_dimensions_unknown",
  embedding_switch_requires_migration:
    "embeddingSwitch.blocker.embedding_switch_requires_migration",
  sdk_reembed_unavailable: "embeddingSwitch.blocker.sdk_reembed_unavailable",
  insufficient_disk_space: "embeddingSwitch.blocker.insufficient_disk_space",
  embedding_switch_running: "embeddingSwitch.blocker.embedding_switch_running",
  memory_lifecycle_busy: "embeddingSwitch.blocker.memory_lifecycle_busy",
};

const failureKeys: Record<string, MessageKey> = {
  writes_did_not_drain: "embeddingSwitch.failure.writes_did_not_drain",
  embedding_service_failed: "embeddingSwitch.failure.embedding_service_failed",
  embedding_dimensions_mismatch:
    "embeddingSwitch.failure.embedding_dimensions_mismatch",
  embedding_verification_failed:
    "embeddingSwitch.failure.embedding_verification_failed",
  target_profile_changed: "embeddingSwitch.failure.target_profile_changed",
  embedding_switch_cancelled:
    "embeddingSwitch.failure.embedding_switch_cancelled",
  model_secret_unavailable: "embeddingSwitch.failure.model_secret_unavailable",
  secure_storage_unavailable:
    "embeddingSwitch.failure.secure_storage_unavailable",
  embedding_switch_clients_busy:
    "embeddingSwitch.failure.embedding_switch_clients_busy",
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
  const { locale, t } = useGuiLanguage();
  const panel = useRef<HTMLElement>(null);
  const blockers = plan.blockers.map((code) =>
    t(blockerKeys[code] ?? "embeddingSwitch.blocker.default"),
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
            <h2 id="embedding-switch-title">
              {t("embeddingSwitch.confirm.title")}
            </h2>
            <p>{t("embeddingSwitch.confirm.description")}</p>
          </div>
        </header>

        <dl className="embedding-switch-impact">
          <div>
            <dt>{t("embeddingSwitch.impact.memories")}</dt>
            <dd>
              {t("embeddingSwitch.impact.memoryCount", {
                count: count(locale, plan.total_vectors),
              })}
              {plan.users && plan.users > 1
                ? t("embeddingSwitch.impact.userCount", {
                    count: count(locale, plan.users),
                  })
                : ""}
            </dd>
          </div>
          <div>
            <dt>{t("embeddingSwitch.impact.duration")}</dt>
            <dd>{duration(locale, t, plan.estimate_s)}</dd>
          </div>
          <div>
            <dt>{t("embeddingSwitch.impact.requests")}</dt>
            <dd>
              {plan.target_local
                ? t("embeddingSwitch.requests.none")
                : plan.requests === null
                  ? t("embeddingSwitch.requests.billed")
                  : t("embeddingSwitch.requests.approx", {
                      count: count(locale, plan.requests),
                    })}
            </dd>
          </div>
        </dl>

        <div className="embedding-switch-safety">
          <strong>{t("embeddingSwitch.safety.searchTitle")}</strong>
          <p>{t("embeddingSwitch.safety.searchBody")}</p>
          <strong>{t("embeddingSwitch.safety.dataTitle")}</strong>
          <p>{t("embeddingSwitch.safety.dataBody")}</p>
        </div>

        <details className="embedding-switch-details">
          <summary>
            <ChevronDown size={14} aria-hidden="true" />
            {t("embeddingSwitch.details.title")}
          </summary>
          <dl>
            <div>
              <dt>{t("embeddingSwitch.details.currentModel")}</dt>
              <dd>
                {plan.source.model ?? t("embeddingSwitch.details.legacyIndex")}
              </dd>
            </div>
            <div>
              <dt>{t("embeddingSwitch.details.newModel")}</dt>
              <dd>
                {plan.target.model ?? t("embeddingSwitch.details.pending")}
              </dd>
            </div>
            <div>
              <dt>{t("embeddingSwitch.details.dimensions")}</dt>
              <dd>
                {plan.source.dims ?? t("embeddingSwitch.details.unknown")} →{" "}
                {plan.target.dims ?? t("embeddingSwitch.details.unknown")}
              </dd>
            </div>
            <div>
              <dt>{t("embeddingSwitch.details.disk")}</dt>
              <dd>{size(locale, plan.disk_bytes_needed)}</dd>
            </div>
            <div>
              <dt>{t("embeddingSwitch.details.retention")}</dt>
              <dd>
                {t("embeddingSwitch.details.days", {
                  count: count(locale, plan.retention_days),
                })}
              </dd>
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
                ? t("embeddingSwitch.error.planStale")
                : t("embeddingSwitch.error.startFailed")}
            </span>
          </div>
        ) : null}

        <footer>
          <R6Button disabled={busy} onClick={onCancel}>
            {t("embeddingSwitch.action.notNow")}
          </R6Button>
          <R6Button
            variant="primary"
            pending={busy}
            disabled={blockers.length > 0}
            onClick={onStart}
          >
            {t("embeddingSwitch.action.start")}
          </R6Button>
        </footer>
      </section>
    </div>
  );
}

function jobTitle(t: Translator, job: EmbeddingSwitchJob) {
  if (job.kind === "merge") return t("embeddingSwitch.job.merge");
  if (job.state === "preparing") return t("embeddingSwitch.job.preparing");
  if (job.state === "reembedding") return t("embeddingSwitch.job.reembedding");
  if (job.state === "verifying") return t("embeddingSwitch.job.verifying");
  if (job.state === "committing") return t("embeddingSwitch.job.committing");
  if (job.state === "failed") return t("embeddingSwitch.job.failed");
  return t("embeddingSwitch.job.default");
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
  const { locale, t } = useGuiLanguage();
  const job = current.job;
  if (!job && current.phase !== "scanning") return null;
  const percent = job?.progress.percent ?? 0;
  return (
    <aside className="embedding-switch-strip" aria-live="polite">
      <div className="embedding-switch-strip-copy">
        <strong>
          {job ? jobTitle(t, job) : t("embeddingSwitch.job.scanning")}
        </strong>
        <span>
          {job?.state === "failed"
            ? t("embeddingSwitch.job.failedSafety")
            : job
              ? `${new Intl.NumberFormat(locale, {
                  style: "percent",
                  maximumFractionDigits: 0,
                }).format(percent / 100)}${
                  job.progress.eta_s === null
                    ? ""
                    : ` · ${duration(locale, t, job.progress.eta_s)}`
                }`
              : t("embeddingSwitch.job.scanPaused")}
        </span>
      </div>
      {job ? (
        <div
          className="embedding-switch-progress"
          role="progressbar"
          aria-label={t("embeddingSwitch.progress.aria")}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(percent)}
        >
          <span style={{ width: `${Math.max(2, percent)}%` }} />
        </div>
      ) : null}
      {job && canCancel(job) ? (
        <R6Button variant="quiet" pending={cancelling} onClick={onCancel}>
          {t("embeddingSwitch.action.cancel")}
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
  const { t } = useGuiLanguage();
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
            <strong>{t("embeddingSwitch.refresh.title")}</strong>
            <span>{t("embeddingSwitch.refresh.body")}</span>
          </div>
          <R6Button variant="quiet" onClick={() => void controller.refresh()}>
            {t("embeddingSwitch.action.refresh")}
          </R6Button>
        </aside>
      ) : null}
      {controller.current &&
      ["failed", "unavailable"].includes(controller.current.scan_state) ? (
        <aside className="embedding-switch-outcome is-error" role="alert">
          <div>
            <strong>{t("embeddingSwitch.scan.title")}</strong>
            <span>
              {controller.current.writes_paused
                ? t("embeddingSwitch.scan.paused")
                : t("embeddingSwitch.scan.recovered")}
            </span>
          </div>
          <R6Button variant="quiet" onClick={onDiagnostics}>
            {t("embeddingSwitch.action.diagnostics")}
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
            <strong>{t("embeddingSwitch.success.title")}</strong>
            <span>{t("embeddingSwitch.success.body")}</span>
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
                {t("embeddingSwitch.action.cleanup")}
              </R6Button>
            ) : null}
            <R6Button
              variant="quiet"
              aria-label={t("embeddingSwitch.action.closeSuccess")}
              onClick={() => setRecent(null)}
            >
              <X size={16} aria-hidden="true" />
            </R6Button>
          </div>
        </aside>
      ) : null}
      {cleanupError ? (
        <p className="embedding-switch-cleanup-error" role="alert">
          {t("embeddingSwitch.cleanup.failed")}
        </p>
      ) : null}
      {recent?.state === "rolled_back" ? (
        <aside
          className="embedding-switch-outcome is-error"
          role="alert"
          aria-live="assertive"
        >
          <div>
            <strong>{t("embeddingSwitch.rollback.title")}</strong>
            <span>
              {failureKeys[recent.error_code ?? ""]
                ? t("embeddingSwitch.rollback.reason", {
                    reason: t(failureKeys[recent.error_code ?? ""]),
                    safety: t("embeddingSwitch.rollback.safety"),
                  })
                : t("embeddingSwitch.rollback.safety")}
            </span>
          </div>
          <div className="embedding-switch-outcome-actions">
            <R6Button variant="quiet" onClick={onDiagnostics}>
              {t("embeddingSwitch.action.diagnostics")}
            </R6Button>
            <R6Button variant="quiet" onClick={onConfigureSearch}>
              {t("embeddingSwitch.action.checkConnection")}
            </R6Button>
            <R6Button
              variant="quiet"
              aria-label={t("embeddingSwitch.action.closeFailure")}
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
