import type { GuiLocale, MessageKey } from "@one-memory/i18n";
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import {
  CALL_HOSTS,
  type CallEvent,
  type CallHistoryPage,
  callDeliveryLabel,
  callDetailHeadline,
  callReturnDeliveryLabel,
  callRowDetail,
  callRowHeadline,
  clearAllCallEvents,
  clearCallEvents,
  getCallHistory,
  getCallReferences,
  type ResolvedCallReference,
} from "./call-history-client";
import { clockStamp } from "./clock-stamp";
import { useGuiLanguage } from "./gui-language";
import { R6Button } from "./R6Controls";
import { traceUiAction } from "./ui-trace";
import { isDefiniteRefusal } from "./write-outcome";
import "./call-history.css";

const PAGE_SIZE = 20;
const stateLabels = {
  grounded: "calls.state.grounded",
  empty: "calls.state.empty",
  partial: "calls.state.partial",
  timed_out: "calls.state.timed_out",
  unavailable: "calls.state.unavailable",
  denied: "calls.state.denied",
} as const satisfies Record<CallEvent["state"], MessageKey>;

function sourceLabel(id: string | null, notRecorded: string) {
  return id === null ? notRecorded : (CALL_HOSTS[id] ?? id);
}

function CallReferences({
  event,
  onOpenMemory,
}: {
  event: CallEvent;
  onOpenMemory?(id: string): void;
}) {
  const { t } = useGuiLanguage();
  const [result, setResult] = useState<{
    event: CallEvent;
    items: ResolvedCallReference[];
  } | null>(null);
  const [failure, setFailure] = useState<CallEvent | null>(null);
  useEffect(() => {
    let stopped = false;
    setResult(null);
    setFailure(null);
    if (event.references.length) {
      void getCallReferences(event)
        .then((items) => {
          if (!stopped) setResult({ event, items });
        })
        .catch(() => {
          if (!stopped) setFailure(event);
        });
    }
    return () => {
      stopped = true;
    };
  }, [event]);
  const resolved = result?.event === event ? result.items : null;
  const failed = failure === event;
  const loading = event.references.length > 0 && !resolved && !failed;
  const references = new Map(
    event.references.map((reference, index) => [
      JSON.stringify([
        reference.kind,
        reference.id,
        reference.revision,
        reference.source_id,
      ]),
      { reference, current: resolved?.[index] },
    ]),
  );
  return (
    <section
      className="call-history-references"
      aria-label={t("calls.references")}
      aria-busy={loading}
    >
      <h3>{t("calls.references")}</h3>
      {loading && <p role="status">{t("calls.referencesLoading")}</p>}
      {failed && <p role="alert">{t("calls.referencesFailed")}</p>}
      {event.references.length ? (
        <ul>
          {Array.from(references, ([key, { reference, current }]) => (
            <li key={key}>
              <div>
                <h4>
                  {t(reference.kind === "raw" ? "calls.raw" : "calls.memory")} ·{" "}
                  {current?.available && current.reason === null
                    ? current.title
                    : current?.reason === "changed"
                      ? t("calls.changed")
                      : loading
                        ? t("calls.pending")
                        : t("calls.notAvailable")}
                </h4>
                {reference.source_id && (
                  <p>
                    {sourceLabel(reference.source_id, t("calls.notRecorded"))}
                  </p>
                )}
              </div>
              {/* A raw reference has no by-id read on the SDK data plane,
                  so only a memory reference opens. */}
              {reference.kind === "memory" &&
                current?.available &&
                current.reason === null && (
                  <R6Button
                    variant="quiet"
                    onClick={() => onOpenMemory?.(reference.id)}
                  >
                    {t("calls.openMemory")}
                  </R6Button>
                )}
            </li>
          ))}
        </ul>
      ) : (
        <p>{t("calls.noReferences")}</p>
      )}
    </section>
  );
}

function shortTime(value: string, locale: GuiLocale) {
  return clockStamp(value, locale);
}

function ClearConfirmation({
  title,
  description,
  confirmLabel,
  pending,
  onCancel,
  onConfirm,
}: {
  title: string;
  description: string;
  confirmLabel: string;
  pending: boolean;
  onCancel(): void;
  onConfirm(): void;
}) {
  const { t } = useGuiLanguage();
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  useEffect(() => {
    const previous = document.activeElement;
    dialog.current?.showModal();
    return () => {
      if (previous instanceof HTMLElement && previous.isConnected)
        previous.focus();
    };
  }, []);
  return (
    <dialog
      ref={dialog}
      className="call-history-confirm"
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      onCancel={(event) => {
        event.preventDefault();
        if (!pending) onCancel();
      }}
    >
      <h2 id={titleId}>{title}</h2>
      <p id={descriptionId}>{description}</p>
      <div className="call-history-actions">
        <R6Button disabled={pending} onClick={onCancel}>
          {t("calls.cancel")}
        </R6Button>
        <R6Button variant="primary" pending={pending} onClick={onConfirm}>
          {confirmLabel}
        </R6Button>
      </div>
    </dialog>
  );
}

export function CallHistory({
  onBack,
  onOpenMemory,
}: {
  onBack(): void;
  onOpenMemory?(id: string): void;
}) {
  const { t, tnPlain, locale } = useGuiLanguage();
  const [request, setRequest] = useState({
    hostId: "",
    offset: 0,
    revision: 0,
  });
  const [result, setResult] = useState<{
    request: typeof request;
    page: CallHistoryPage;
  } | null>(null);
  const [failure, setFailure] = useState<typeof request | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<
    { kind: "page"; ids: string[] } | { kind: "all" } | null
  >(null);
  const [clearing, setClearing] = useState(false);
  const [clearNotice, setClearNotice] = useState<MessageKey | null>(null);
  const mounted = useRef(false);
  const clearFlight = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const previousSelection = useRef<string | null>(null);
  const titleId = useId();
  const filterId = useId();

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    let stopped = false;
    setResult(null);
    setFailure(null);
    void getCallHistory({
      limit: PAGE_SIZE,
      offset: request.offset,
      ...(request.hostId ? { hostId: request.hostId } : {}),
    })
      .then((page) => {
        if (stopped) return;
        setResult({ request, page });
      })
      .catch(() => {
        if (!stopped) setFailure(request);
      });
    return () => {
      stopped = true;
    };
  }, [request]);

  useEffect(() => {
    if (selectedId !== null) heading.current?.focus();
    else if (previousSelection.current !== null) {
      const row = Array.from(
        list.current?.querySelectorAll<HTMLButtonElement>("[data-event-id]") ??
          [],
      ).find((button) => button.dataset.eventId === previousSelection.current);
      (row ?? heading.current)?.focus();
    }
    previousSelection.current = selectedId;
  }, [selectedId]);

  const page = result?.request === request && !clearing ? result.page : null;
  const failed = failure === request;
  const loading = !page && !failed && !clearing;
  const events = page?.available ? page.items : [];
  const selected =
    selectedId === null
      ? null
      : events.find((item) => item.event_id === selectedId);
  const returnedFrom =
    selected?.references.find((item) => item.source_id)?.source_id ?? null;
  const refresh = () =>
    setRequest((current) => ({ ...current, revision: current.revision + 1 }));

  async function confirmClear() {
    if (!confirmation || clearFlight.current) return;
    const clearingAll = confirmation.kind === "all";
    clearFlight.current = true;
    setClearing(true);
    setClearNotice(null);
    const trace = traceUiAction("calls", "confirm");
    try {
      if (clearingAll) await clearAllCallEvents(trace.traceId);
      else await clearCallEvents([...confirmation.ids], trace.traceId);
      trace.done("succeeded");
      if (!mounted.current) return;
      setClearNotice(clearingAll ? "calls.clearedAll" : "calls.clearedPage");
    } catch (reason) {
      // A 4xx (e.g. `409 call_history_disabled`) is Core answering and
      // refusing: nothing was deleted, so it is a definite failure, not an
      // unconfirmed one. Everything else (timeout/5xx/transport) may or may
      // not have landed and stays `unknown` with the no-auto-retry notice.
      const refused = isDefiniteRefusal(reason);
      trace.done(refused ? "failed" : "unknown", {
        reason: refused
          ? clearingAll
            ? "clear_all_failed"
            : "clear_selected_failed"
          : clearingAll
            ? "clear_all_unconfirmed"
            : "clear_selected_unconfirmed",
      });
      if (!mounted.current) return;
      setClearNotice(
        refused
          ? clearingAll
            ? "calls.clearAllFailed"
            : "calls.clearPageFailed"
          : "calls.clearUnknown",
      );
    } finally {
      clearFlight.current = false;
      if (mounted.current) {
        setClearing(false);
        setConfirmation(null);
        setSelectedId(null);
        setRequest((current) => ({
          ...current,
          offset: clearingAll ? 0 : current.offset,
          revision: current.revision + 1,
        }));
      }
    }
  }

  return (
    <section className="call-history" aria-labelledby={titleId}>
      <header className="call-history-heading">
        <h1 id={titleId} ref={heading} tabIndex={-1}>
          {t(selectedId === null ? "calls.title" : "calls.query")}
        </h1>
        <R6Button
          className="call-history-back"
          disabled={clearing}
          onClick={selectedId === null ? onBack : () => setSelectedId(null)}
        >
          <ArrowLeft size={16} aria-hidden="true" />
          {t(selectedId === null ? "calls.backApps" : "calls.backHistory")}
        </R6Button>
      </header>
      <div
        className={`call-history-toolbar${selectedId !== null ? " call-history-detail-toolbar" : ""}`}
      >
        {selectedId === null ? (
          <div className="call-history-filter">
            <label className="call-history-sr-only" htmlFor={filterId}>
              {t("calls.filter")}
            </label>
            <select
              id={filterId}
              value={request.hostId}
              disabled={clearing}
              onChange={(event) => {
                setSelectedId(null);
                setRequest((current) => ({
                  hostId: event.target.value,
                  offset: 0,
                  revision: current.revision + 1,
                }));
              }}
            >
              <option value="">{t("calls.allApps")}</option>
              {Object.entries(CALL_HOSTS).map(([host, label]) => (
                <option key={host} value={host}>
                  {label}
                </option>
              ))}
            </select>
          </div>
        ) : (
          <span />
        )}
        <div className="call-history-actions">
          {selectedId === null && <span>{t("calls.recent")}</span>}
          <R6Button
            className="call-history-icon"
            variant="quiet"
            title={t("calls.refresh")}
            aria-label={t("calls.refresh")}
            disabled={clearing}
            onClick={refresh}
          >
            <RefreshCw size={18} aria-hidden="true" />
          </R6Button>
          {selectedId === null && (
            <>
              <R6Button
                className="call-history-icon"
                variant="quiet"
                title={t("calls.clearPage")}
                aria-label={t("calls.clearPage")}
                disabled={!events.length || clearing}
                onClick={() =>
                  setConfirmation({
                    kind: "page",
                    ids: events.map((item) => item.event_id),
                  })
                }
              >
                <Trash2 size={18} aria-hidden="true" />
              </R6Button>
              <R6Button
                variant="quiet"
                title={t("calls.clearAll")}
                aria-label={t("calls.clearAll")}
                disabled={!page?.total || clearing}
                onClick={() => setConfirmation({ kind: "all" })}
              >
                {t("calls.clearAllShort")}
              </R6Button>
            </>
          )}
        </div>
      </div>
      {clearNotice && (
        <p className="call-history-notice" role="status">
          {t(clearNotice)}
        </p>
      )}
      <div aria-busy={loading || clearing}>
        {loading && (
          <p className="call-history-state" role="status">
            {t("calls.loading")}
          </p>
        )}
        {clearing && (
          <p className="call-history-state" role="status">
            {t("calls.clearing")}
          </p>
        )}
        {failed && (
          <p className="call-history-state" role="alert">
            {t("calls.readFailed")}
          </p>
        )}
        {page && !page.available && (
          <p className="call-history-state" role="alert">
            {t("calls.unavailable")}
          </p>
        )}
        {page?.recording_error && (
          <p className="call-history-notice" role="alert">
            {t("calls.incomplete")}
          </p>
        )}
        {page?.available &&
          (selectedId !== null ? (
            selected ? (
              <article
                className="call-history-detail"
                aria-label={t("calls.callDetail")}
              >
                <h2>{`${sourceLabel(selected.host_id, t("calls.notRecorded"))} · ${shortTime(selected.occurred_at, locale)}`}</h2>
                <p className="call-history-summary">
                  {callDetailHeadline(selected, undefined, locale)}
                  {returnedFrom
                    ? t("calls.source", {
                        source: sourceLabel(
                          returnedFrom,
                          t("calls.notRecorded"),
                        ),
                      })
                    : ""}
                </p>
                <p className="call-history-stage">
                  {t(stateLabels[selected.state])}
                </p>
                <p className="call-history-stage">
                  {t("calls.stats", {
                    raw: selected.raw_count,
                    memories: selected.memory_count,
                    seconds: (selected.elapsed_ms / 1000).toFixed(1),
                  })}
                </p>
                {callDeliveryLabel(selected, locale) && (
                  <p className="call-history-stage">
                    {callDeliveryLabel(selected, locale)}
                  </p>
                )}
                <CallReferences event={selected} onOpenMemory={onOpenMemory} />
                {callReturnDeliveryLabel(selected, locale) && (
                  <p className="call-history-adoption">
                    {callReturnDeliveryLabel(selected, locale)}
                  </p>
                )}
                <section
                  className="call-history-metadata"
                  aria-label={t("calls.metadata")}
                >
                  <h3>{t("calls.metadata")}</h3>
                  <p>{t("calls.privacy")}</p>
                  <dl>
                    <div>
                      <dt>{t("calls.exactTime")}</dt>
                      <dd>
                        <time dateTime={selected.occurred_at}>
                          {selected.occurred_at}
                        </time>
                      </dd>
                    </div>
                    <div>
                      <dt>{t("calls.app")}</dt>
                      <dd>
                        {sourceLabel(selected.host_id, t("calls.notRecorded"))}
                      </dd>
                    </div>
                    <div>
                      <dt>{t("calls.type")}</dt>
                      <dd>{t("calls.tool")}</dd>
                    </div>
                    <div>
                      <dt>{t("calls.result")}</dt>
                      {/* The tool above is already humanized in this same
                        panel ("查记忆", never `om_recall`); printing the raw
                        `grounded`/`partial` enum here leaks the internal state
                        vocabulary the rest of the page translates. */}
                      <dd>{t(stateLabels[selected.state])}</dd>
                    </div>
                  </dl>
                </section>
              </article>
            ) : (
              <p className="call-history-state" role="status">
                {t("calls.missingRow")}
              </p>
            )
          ) : (
            <>
              <ul
                ref={list}
                className="call-history-list"
                aria-label={t("calls.list")}
              >
                {events.map((item) => (
                  <li key={item.event_id}>
                    <time dateTime={item.occurred_at} title={item.occurred_at}>
                      {shortTime(item.occurred_at, locale)}
                    </time>
                    <div className="call-history-row-content">
                      <h2>
                        <button
                          type="button"
                          data-event-id={item.event_id}
                          onClick={() => setSelectedId(item.event_id)}
                        >
                          {callRowHeadline(item, locale)}
                        </button>
                      </h2>
                      <p>{callRowDetail(item, locale)}</p>
                    </div>
                  </li>
                ))}
              </ul>
              {!events.length && (
                <p className="call-history-state" role="status">
                  {t(request.offset ? "calls.emptyPage" : "calls.empty")}
                </p>
              )}
              <nav
                className="call-history-pagination"
                aria-label={t("calls.pagination")}
              >
                <R6Button
                  aria-label={t("calls.previous")}
                  title={t("calls.previous")}
                  disabled={request.offset === 0}
                  onClick={() =>
                    setRequest((current) => ({
                      ...current,
                      offset: Math.max(0, current.offset - PAGE_SIZE),
                    }))
                  }
                >
                  <ChevronLeft size={18} aria-hidden="true" />
                </R6Button>
                <span>
                  {events.length
                    ? `${page.offset + 1}–${page.offset + events.length}`
                    : "0"}{" "}
                  / {page.total}
                </span>
                <R6Button
                  aria-label={t("calls.next")}
                  title={t("calls.next")}
                  disabled={
                    page.offset + PAGE_SIZE >= page.total || !events.length
                  }
                  onClick={() =>
                    setRequest((current) => ({
                      ...current,
                      offset: current.offset + PAGE_SIZE,
                    }))
                  }
                >
                  <ChevronRight size={18} aria-hidden="true" />
                </R6Button>
              </nav>
            </>
          ))}
      </div>
      {page?.available &&
        page.retention_days !== null &&
        page.max_events !== null && (
          <p className="call-history-policy">
            {t("calls.policy", {
              days: page.retention_days,
              count: new Intl.NumberFormat(locale).format(page.max_events),
            })}
          </p>
        )}
      {confirmation && (
        <ClearConfirmation
          title={
            confirmation.kind === "all"
              ? t("calls.confirmAllTitle")
              : tnPlain("calls.confirmPageTitle", confirmation.ids.length)
          }
          description={
            confirmation.kind === "all"
              ? t("calls.confirmAllDescription")
              : t("calls.confirmPageDescription")
          }
          confirmLabel={t(
            confirmation.kind === "all"
              ? "calls.confirmAll"
              : "calls.confirmPage",
          )}
          pending={clearing}
          onCancel={() => setConfirmation(null)}
          onConfirm={() => void confirmClear()}
        />
      )}
    </section>
  );
}
