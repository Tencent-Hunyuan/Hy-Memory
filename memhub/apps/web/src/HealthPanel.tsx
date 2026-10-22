import { type GuiLocale, translate } from "@one-memory/i18n";
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
import { useGuiLanguage } from "./gui-language";
import { refreshRecallHosts } from "./host-state";
import { memoryText } from "./memory-language";
import {
  blockReasonLabel,
  splitBlockedStages,
  taskKindLabel,
} from "./memory-processing-labels";
import { subscribePoll } from "./poll-scheduler";
import { R6Button, R6Switch } from "./R6Controls";
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

function ReadTime({ label, value }: { label: string; value: string }) {
  const { locale } = useGuiLanguage();
  return (
    <p className="health-stamp">
      {label}
      <time dateTime={value}>{clockStamp(value, locale)}</time>
    </p>
  );
}

function ReadReceipt({
  pending,
  confirmedAt,
  failedAt,
}: Pick<Reading<unknown>, "pending" | "confirmedAt" | "failedAt">) {
  const { t } = useGuiLanguage();
  return (
    <>
      {failedAt ? (
        <p role="status">
          {t("healthReadFailed")}
          {confirmedAt ? t("healthStale") : "。"}
        </p>
      ) : null}
      {pending ? (
        <p role="status">
          {confirmedAt ? t("healthRereading") : t("healthReading")}
        </p>
      ) : null}
      {confirmedAt ? (
        <ReadTime label={t("healthReadSuccessTime")} value={confirmedAt} />
      ) : null}
      {failedAt ? (
        <ReadTime label={t("healthReadFailedTime")} value={failedAt} />
      ) : null}
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
  const { t, tn, tnPlain, locale } = useGuiLanguage();
  const n = (value: number) => value.toLocaleString(locale);
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
  const [diagnosticMessage, setDiagnosticMessage] = useState("");
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
      setDiagnosticMessage(t("healthIncidentDesktop"));
      return;
    }
    setDiagnosticAction("incident");
    setDiagnosticMessage("");
    try {
      const description = window.prompt(t("healthIncidentPrompt"), "");
      if (description === null) {
        setDiagnosticAction(null);
        return;
      }
      const receipt =
        await window.oneMemory.markDiagnosticIncident(description);
      await window.oneMemory.copyText?.(receipt.incident_id);
      setDiagnosticMessage(
        t("healthIncidentCopied", { id: receipt.incident_id }),
      );
    } catch {
      setDiagnosticMessage(t("healthIncidentError"));
    } finally {
      setDiagnosticAction(null);
    }
  }

  async function exportDiagnostics() {
    if (!window.oneMemory?.exportDiagnostics) {
      setDiagnosticMessage(t("diagnosticExportDesktop"));
      return;
    }
    setDiagnosticAction("export");
    setDiagnosticMessage("");
    try {
      const result = await exportDiagnosticsFile(locale);
      setDiagnosticMessage(result.message);
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
        <p role="status">{t("healthReadingSources")}</p>
      ) : sourceUnavailable ? (
        <p role="status">
          {t("healthReadFailed")}
          {sourceConfirmedAt || imports.health || imports.sources.length
            ? t("healthStale")
            : "。"}
        </p>
      ) : null}
      {sourceConfirmedAt ? (
        <ReadTime
          label={t("healthReadSuccessTime")}
          value={sourceConfirmedAt}
        />
      ) : null}
      {sourceUnavailable && sourceFailedAt ? (
        <ReadTime label={t("healthReadFailedTime")} value={sourceFailedAt} />
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
          <h1 id="health-title">{t("healthTitle")}</h1>
        </div>
      </header>

      <div className="health-actions health-actions-primary">
        <R6Button
          variant="primary"
          pending={diagnosticAction === "export"}
          onClick={() => void exportDiagnostics()}
        >
          <Download size={16} aria-hidden="true" />
          {t("nativeExportLogs")}
        </R6Button>
      </div>

      <section className="health-block" aria-label={t("healthLocal")}>
        <h2>{t("healthLocal")}</h2>
        {sourceReceipt}
        {imports.health ? (
          <p>
            {t("healthCoreVersion", {
              state: t(
                sourceUnavailable || imports.loading
                  ? "healthPrevious"
                  : "healthRunning",
              ),
              version: imports.health.version,
              instance: imports.health.instance_id,
            })}
          </p>
        ) : !sourceUnavailable && !imports.loading ? (
          <p>{t("healthCoreDisconnected")}</p>
        ) : null}
      </section>

      <section className="health-block" aria-label={t("healthBackground")}>
        <h2>{t("healthBackground")}</h2>
        {backgroundRead.value === "no-bridge" ? (
          <p>{t("healthBackgroundUnavailable")}</p>
        ) : (
          <>
            <ReadReceipt {...backgroundRead} />
            {backgroundRead.value ? (
              <>
                <p>
                  {backgroundRead.pending || backgroundRead.failedAt
                    ? t("healthPreviousPrefix")
                    : ""}
                  {backgroundVerdict(backgroundRead.value.state, locale)}
                </p>
                <p>
                  {t("healthRuntime", {
                    core: runtimeStateLabel(backgroundRead.value.core, locale),
                    broker: runtimeStateLabel(
                      backgroundRead.value.broker,
                      locale,
                    ),
                  })}
                </p>
              </>
            ) : null}
          </>
        )}
      </section>

      <section className="health-block" aria-label={t("healthSources")}>
        <h2>{t("healthSources")}</h2>
        {sourceReceipt}
        {imports.sources.length ? (
          <dl>
            {imports.sources.map((source) => (
              <div key={source.source_id}>
                <dt>{source.display_name}</dt>
                <dd>
                  {t("healthSourceCount", {
                    conversations: n(source.imported_conversations),
                    messages: n(source.imported_messages),
                  })}
                </dd>
              </div>
            ))}
          </dl>
        ) : !sourceUnavailable && !imports.loading ? (
          <p>{t("healthNoSources")}</p>
        ) : null}
      </section>

      <section className="health-block" aria-label={t("healthProcessing")}>
        <h2>{t("healthProcessing")}</h2>
        <ReadReceipt {...processingRead} />
        {processing ? (
          <>
            <p>
              {t("healthTaskCounts", {
                running: n(processing.running),
                pending: n(processing.pending),
                completed: n(processing.completed),
                failed: n(processing.failed),
              })}
            </p>
            {processing.metrics ? (
              <dl aria-label={t("healthImportDetails")}>
                <div>
                  <dt>{t("healthRawTurns")}</dt>
                  <dd>
                    {t("healthRawCounts", {
                      raw: n(processing.metrics.raw_evidence),
                      turns: n(processing.metrics.complete_turns),
                    })}
                  </dd>
                </div>
                <div>
                  <dt>{t("healthHy")}</dt>
                  <dd>
                    {t("healthHyCounts", {
                      calls: n(processing.metrics.hy_calls),
                      completed: n(processing.metrics.completed),
                      unknown: n(processing.metrics.unknown),
                      failed: n(processing.metrics.failed),
                      cancelled: n(processing.metrics.cancelled),
                    })}
                  </dd>
                </div>
                <div>
                  <dt>{t("healthOutput")}</dt>
                  <dd>
                    {tn("healthMemories", processing.metrics.governed_memories)}
                  </dd>
                </div>
                <div>
                  <dt>{t("healthWorkbuddy")}</dt>
                  <dd>
                    {t("healthWorkbuddyCounts", {
                      injected: n(processing.metrics.workbuddy.injected),
                      timedOut: n(processing.metrics.workbuddy.timed_out),
                      unavailable: n(processing.metrics.workbuddy.unavailable),
                    })}
                  </dd>
                </div>
              </dl>
            ) : null}
            {processing.blocked_stages.length ? (
              <ul>
                {splitBlockedStages(processing.blocked_stages).named.map(
                  (block) => (
                    <li key={`${block.kind}:${block.reason}`}>
                      {t("healthBlocked", {
                        kind: memoryText(locale, taskKindLabel(block.kind)),
                        reason: memoryText(
                          locale,
                          blockReasonLabel(block.reason),
                        ),
                        count: n(block.count),
                      })}
                    </li>
                  ),
                )}
                {retrying ? (
                  <li>{tnPlain("healthRetrying", retrying)}</li>
                ) : null}
              </ul>
            ) : !processingRead.failedAt && !processingRead.pending ? (
              <p>{t("healthNoIssues")}</p>
            ) : null}
          </>
        ) : null}
      </section>

      <section className="health-block" aria-label={t("healthHosts")}>
        <h2>{t("healthHosts")}</h2>
        {/* Host state lives in the desktop shell. In a browser it is not
            false, it is unavailable, and reporting it as 未接通 would send
            the user to repair links that may well be fine. */}
        {hosts === "no-bridge" || !window.oneMemory ? (
          <p>{t("healthHostsUnavailable")}</p>
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
                        ? t("healthPreviousPrefix")
                        : ""}
                      {hostVerdict(host, locale)}
                    </dd>
                  </div>
                ))}
              </dl>
            ) : hosts && !hostsRead.failedAt && !hostsRead.pending ? (
              <p>{t("healthNoHosts")}</p>
            ) : null}
          </>
        )}
      </section>
      <section className="health-block" aria-label={t("healthDetailed")}>
        <div className="health-setting-row">
          <div>
            <h2>{t("healthDetailedTitle")}</h2>
            <p>{t("healthDetailedHint")}</p>
          </div>
          <R6Switch
            label={t("healthDetailedTitle")}
            checked={detailedLogging}
            pending={detailedLoggingPending}
            onCheckedChange={(enabled) => {
              setDetailedLoggingPending(true);
              void setDetailedLogging(enabled)
                .then(setDetailedLoggingState)
                .catch(() => setDiagnosticMessage(t("healthDetailedError")))
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
          {t("healthRefresh")}
        </R6Button>
        <R6Button
          variant="quiet"
          pending={diagnosticAction === "incident"}
          onClick={() => void markIncident()}
        >
          <Flag size={16} aria-hidden="true" />
          {t("healthIncident")}
        </R6Button>
      </div>
      {diagnosticMessage ? (
        <p className="health-diagnostic-message" role="status">
          {diagnosticMessage}
        </p>
      ) : null}
    </section>
  );
}

/** A host with no self-check on record has not been shown to work, and the
 * install flow's own wording for that is 未接通. */
function hostVerdict(host: RecallHostStatus, locale: GuiLocale): string {
  return translate(
    locale,
    !host.installed
      ? "healthUnreachable"
      : host.selfCheck?.state === "wired"
        ? "healthWired"
        : host.selfCheck?.state === "reachable_but_blind"
          ? "healthBlind"
          : "healthUnreachable",
  );
}

function backgroundVerdict(
  state: BackgroundSnapshot["state"],
  locale: GuiLocale,
): string {
  return translate(
    locale,
    (
      {
        healthy: "healthBackgroundHealthy",
        stopped: "healthBackgroundStopped",
        degraded: "healthBackgroundDegraded",
        untrusted: "healthBackgroundUntrusted",
        unknown: "healthBackgroundUnknown",
      } as const
    )[state],
  );
}

function runtimeStateLabel(
  state: BackgroundSnapshot["core"] | BackgroundSnapshot["broker"],
  locale: GuiLocale,
): string {
  return translate(
    locale,
    (
      {
        running: "healthRunning",
        stopped: "healthStopped",
        unhealthy: "healthUnhealthy",
        untrusted: "healthUntrusted",
        unknown: "healthUnknown",
        not_checked: "healthNotChecked",
      } as const
    )[state],
  );
}
