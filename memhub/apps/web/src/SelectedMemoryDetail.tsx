import type {
  MemoryDetail,
  SdkMemoryStatus,
  SourceResponse,
} from "@one-memory/contracts";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { apiErrorCode, correctMemory, getMemory, mutateMemory } from "./api";
import { clockStamp } from "./clock-stamp";
import { useGuiLanguage } from "./gui-language";
import { R6Button } from "./R6Controls";
import { sourceNameOr } from "./source-label";
import { traceUiAction } from "./ui-trace";
import { isDefiniteRefusal, writeTraceStatus } from "./write-outcome";
import "./selected-memory.css";

type Page = "read" | "edit" | "delete";

export function SelectedMemoryDetail({
  detail,
  sources,
  onChanged,
  suspended = false,
  hideActions = false,
  conversationAt = null,
}: {
  detail: MemoryDetail;
  sources: SourceResponse[];
  onChanged(id: string): void;
  suspended?: boolean;
  hideActions?: boolean;
  /** D99: when the cited conversation happened, in the list's own terms. */
  conversationAt?: { at: string; basis: "conversation" | "import" } | null;
}) {
  const { locale, t } = useGuiLanguage();
  const [page, setPage] = useState<Page>("read");
  const [draft, setDraft] = useState(detail.memory.content);
  const [base, setBase] = useState(detail.memory.revision);
  const [conflict, setConflict] = useState<MemoryDetail | null>(null);
  const [conflictFound, setConflictFound] = useState(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [showHistory, setShowHistory] = useState(false);
  const flight = useRef(false),
    alive = useRef(true),
    request = useRef<string | null>(null),
    trigger = useRef<HTMLButtonElement | null>(null),
    focus = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const memory = detail.memory;
  const evidence = detail.evidence[0];
  const sdkStatusLabels: Record<SdkMemoryStatus, Parameters<typeof t>[0]> = {
    active: "selectedMemory.status.active",
    superseded: "selectedMemory.status.superseded",
    shadow: "selectedMemory.status.shadow",
    negated: "selectedMemory.status.negated",
    conflicted: "selectedMemory.status.conflicted",
    archived: "selectedMemory.status.archived",
  };
  const statusLabel = detail.sdk_status
    ? t(sdkStatusLabels[detail.sdk_status])
    : memory.status === "confirmed"
      ? t("selectedMemory.status.confirmed")
      : memory.status === "deleted"
        ? t("selectedMemory.status.deleted")
        : memory.status === "superseded"
          ? t("selectedMemory.status.superseded")
          : t("selectedMemory.status.unknown");
  const source = sourceNameOr(
    sources,
    detail.source_id ?? evidence?.source_id,
    t("selectedMemory.sourceUnknown"),
  );
  const dirty = page === "edit" && draft !== memory.content;
  useEffect(() => {
    if (page === "edit" && memory.revision > base) setConflictFound(true);
  }, [page, memory.revision, base]);
  useEffect(() => {
    if (page === "read" || suspended) return;
    const previousFocus = trigger.current;
    const root = document.querySelector<HTMLElement>(".r6-shell");
    const previous = root?.inert;
    if (root) root.inert = true;
    focus.current?.focus({ preventScroll: true });
    return () => {
      if (root) root.inert = previous ?? false;
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected)
        previousFocus.focus({ preventScroll: true });
    };
  }, [page, suspended]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  function close() {
    if (busy) return;
    if (dirty && !window.confirm(t("selectedMemory.discardDraft"))) return;
    setPage("read");
    setError("");
    setConflictFound(false);
    setConflict(null);
    request.current = null;
  }
  async function refreshConflict() {
    setBusy(true);
    try {
      const latest = await getMemory(memory.memory_id);
      if (alive.current) setConflict(latest);
    } catch {
      if (alive.current) setError(t("selectedMemory.newVersionReadFailed"));
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  async function save() {
    if (flight.current || conflictFound || !draft.trim()) return;
    flight.current = true;
    setBusy(true);
    setError("");
    request.current ??= crypto.randomUUID();
    const trace = traceUiAction("memory", "submit");
    try {
      await correctMemory(
        memory.memory_id,
        base,
        draft,
        undefined,
        request.current,
        trace.traceId,
      );
      trace.done("succeeded");
      if (!alive.current) return;
      request.current = null;
      setPage("read");
      onChanged(memory.memory_id);
    } catch (reason) {
      trace.done(writeTraceStatus(reason), {
        reason:
          apiErrorCode(reason) === "memory_revision_conflict"
            ? "memory_revision_conflict"
            : isDefiniteRefusal(reason)
              ? "memory_correct_failed"
              : "memory_correct_result_unknown",
      });
      if (alive.current) {
        if (apiErrorCode(reason) === "memory_revision_conflict") {
          setConflictFound(true);
          setError("");
        } else if (isDefiniteRefusal(reason))
          setError(t("selectedMemory.updateRejected"));
        else setError(t("selectedMemory.updateUnknown"));
      }
    } finally {
      flight.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function remove() {
    if (flight.current) return;
    flight.current = true;
    setBusy(true);
    setError("");
    request.current ??= crypto.randomUUID();
    const trace = traceUiAction("memory", "confirm");
    try {
      await mutateMemory(
        memory.memory_id,
        "delete",
        memory.revision,
        request.current,
        trace.traceId,
      );
      trace.done("succeeded");
      if (alive.current) {
        setPage("read");
        request.current = null;
        onChanged(memory.memory_id);
      }
    } catch (reason) {
      const refused = isDefiniteRefusal(reason);
      trace.done(refused ? "failed" : "unknown", {
        reason: refused ? "memory_delete_failed" : "memory_delete_unconfirmed",
      });
      if (alive.current)
        setError(
          refused
            ? t("selectedMemory.deleteRejected")
            : t("selectedMemory.deleteUnknown"),
        );
    } finally {
      flight.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function govern(action: "undo") {
    if (flight.current) return;
    flight.current = true;
    setBusy(true);
    setError("");
    const trace = traceUiAction("memory", "retry");
    try {
      await mutateMemory(
        memory.memory_id,
        action,
        memory.revision,
        crypto.randomUUID(),
        trace.traceId,
      );
      trace.done("succeeded");
      if (alive.current) onChanged(memory.memory_id);
    } catch (reason) {
      const refused = isDefiniteRefusal(reason);
      trace.done(refused ? "failed" : "unknown", {
        reason: refused ? "memory_undo_failed" : "memory_undo_unconfirmed",
      });
      if (alive.current)
        setError(
          refused
            ? t("selectedMemory.undoRejected")
            : t("selectedMemory.undoUnknown"),
        );
    } finally {
      flight.current = false;
      if (alive.current) setBusy(false);
    }
  }
  const split = memory.content.search(/[。\n]/);
  const heading =
    split > 0 && split < 85
      ? memory.content.slice(0, split + 1)
      : memory.content;
  const body =
    split > 0 && split < 85 ? memory.content.slice(split + 1).trim() : "";
  return (
    <>
      <article
        className="selected-memory-detail"
        data-hide-actions={hideActions ? "true" : "false"}
      >
        <p className="selected-memory-status">
          {statusLabel}
          {!detail.sdk_status &&
            ` · ${t("selectedMemory.version", {
              revision: new Intl.NumberFormat(locale).format(memory.revision),
            })}`}
        </p>
        <h3>{heading}</h3>
        {body && <p className="selected-memory-body">{body}</p>}
        {detail.source_id || detail.evidence.length ? (
          <p className="selected-memory-source">
            {t("selectedMemory.sourceLine", {
              source,
              time: clockStamp(
                detail.sdk_memory_at ?? evidence?.created_at,
                locale,
              ),
            })}
          </p>
        ) : null}
        <p className="selected-memory-times">
          {t("selectedMemory.timestamps", {
            conversation: clockStamp(
              conversationAt?.at ??
                detail.sdk_memory_at ??
                evidence?.created_at,
              locale,
            ),
            created: clockStamp(
              detail.sdk_created_at ?? memory.updated_at,
              locale,
            ),
          })}
        </p>
        {!hideActions && (
          <R6Button
            onClick={(event) => {
              trigger.current = event.currentTarget;
              setDraft(memory.content);
              setBase(memory.revision);
              setConflictFound(false);
              setConflict(null);
              request.current = null;
              setPage("edit");
            }}
          >
            {t("selectedMemory.edit")}
          </R6Button>
        )}
        {!hideActions && (
          <R6Button variant="quiet" onClick={() => setShowHistory((v) => !v)}>
            {t("selectedMemory.history")}
          </R6Button>
        )}
        {!hideActions && showHistory && (
          <ol className="selected-memory-history">
            {detail.history.map((v) => (
              <li key={v.revision}>
                <small>
                  {t("selectedMemory.version", {
                    revision: new Intl.NumberFormat(locale).format(v.revision),
                  })}
                </small>
                <p>{v.content}</p>
              </li>
            ))}
          </ol>
        )}
        {!hideActions && (
          <details className="selected-memory-more">
            <summary>{t("selectedMemory.more")}</summary>
            {detail.history.length > 1 && (
              <R6Button pending={busy} onClick={() => void govern("undo")}>
                {t("selectedMemory.undo")}
              </R6Button>
            )}
            <R6Button
              onClick={(event) => {
                trigger.current = event.currentTarget;
                request.current = null;
                setPage("delete");
              }}
            >
              {t("selectedMemory.delete")}
            </R6Button>
          </details>
        )}
        {error && page === "read" && <p role="alert">{error}</p>}
      </article>
      {page !== "read" &&
        createPortal(
          <section
            className="selected-memory-page"
            hidden={suspended}
            aria-label={
              page === "edit"
                ? t("selectedMemory.editTitle")
                : t("selectedMemory.delete")
            }
            onKeyDown={(event) => {
              if (event.key === "Tab") {
                const controls = Array.from(
                  event.currentTarget.querySelectorAll<HTMLElement>(
                    'button:not(:disabled),textarea:not(:disabled),[tabindex="0"]',
                  ),
                );
                const first = controls[0],
                  last = controls.at(-1);
                if (
                  event.shiftKey &&
                  (document.activeElement === first ||
                    document.activeElement === focus.current)
                ) {
                  event.preventDefault();
                  last?.focus();
                } else if (!event.shiftKey && document.activeElement === last) {
                  event.preventDefault();
                  first?.focus();
                }
              }
              if (event.key === "Escape") {
                event.preventDefault();
                close();
              }
            }}
          >
            <header>
              <h1 ref={focus} tabIndex={-1}>
                {page === "edit"
                  ? t("selectedMemory.editTitle")
                  : t("selectedMemory.delete")}
              </h1>
            </header>
            {page === "edit" ? (
              <div className="selected-editor">
                <p>{t("selectedMemory.editDescription")}</p>
                <textarea
                  aria-label={t("selectedMemory.content")}
                  name="memory-content"
                  value={draft}
                  disabled={busy}
                  maxLength={1000000}
                  onChange={(e) => {
                    setDraft(e.target.value);
                    request.current = null;
                  }}
                />
                <p>{t("selectedMemory.scopeUnchanged")}</p>
                {conflictFound && (
                  <section className="selected-conflict">
                    <h2>{t("selectedMemory.conflictTitle")}</h2>
                    <p>{t("selectedMemory.conflictDescription")}</p>
                    <R6Button
                      pending={busy}
                      onClick={() => void refreshConflict()}
                    >
                      {t("selectedMemory.viewNewVersion")}
                    </R6Button>
                    {conflict && (
                      <>
                        <blockquote>{conflict.memory.content}</blockquote>
                        <R6Button
                          onClick={() => {
                            setBase(conflict.memory.revision);
                            setConflictFound(false);
                            request.current = null;
                          }}
                        >
                          {t("selectedMemory.continueFromVersion")}
                        </R6Button>
                      </>
                    )}
                  </section>
                )}
                <div className="selected-page-actions">
                  <R6Button disabled={busy} onClick={close}>
                    {t("selectedMemory.cancel")}
                  </R6Button>
                  <R6Button
                    variant="primary"
                    pending={busy}
                    disabled={!draft.trim() || conflictFound}
                    onClick={() => void save()}
                  >
                    {t("selectedMemory.save")}
                  </R6Button>
                </div>
              </div>
            ) : (
              <div className="selected-delete">
                <h2>{t("selectedMemory.deleteTitle")}</h2>
                <p>{heading}</p>
                <p>
                  {t("selectedMemory.deleteDescription")}
                  <br />
                  {t("selectedMemory.sentCannotRecall")}
                </p>
                <div className="selected-page-actions">
                  <R6Button disabled={busy} onClick={close}>
                    {t("selectedMemory.cancel")}
                  </R6Button>
                  <R6Button
                    variant="primary"
                    pending={busy}
                    onClick={() => void remove()}
                  >
                    {t("selectedMemory.delete")}
                  </R6Button>
                </div>
              </div>
            )}
            {error && (
              <p className="selected-page-error" role="alert">
                {error}
              </p>
            )}
          </section>,
          document.body,
        )}
    </>
  );
}
