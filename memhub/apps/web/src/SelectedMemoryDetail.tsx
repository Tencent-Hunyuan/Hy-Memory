import type {
  MemoryDetail,
  SourceResponse,
  UiMessage,
} from "@one-memory/contracts";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { apiErrorCode, correctMemory, getMemory, mutateMemory } from "./api";
import { clockStamp } from "./clock-stamp";
import { MemoryRecallScope } from "./MemoryRecallScope";
import { R6Button } from "./R6Controls";
import { sourceNameOr } from "./source-label";
import { useTranslate, useUiLanguage } from "./ui-language";
import { traceUiAction } from "./ui-trace";
import { isDefiniteRefusal, writeTraceStatus } from "./write-outcome";
import "./selected-memory.css";

type Page = "read" | "edit" | "original" | "delete";

export function SelectedMemoryDetail({
  detail,
  sources,
  onChanged,
  onSaveScopes,
  suspended = false,
  hideActions = false,
  conversationAt = null,
}: {
  detail: MemoryDetail;
  sources: SourceResponse[];
  onChanged(id: string): void;
  onSaveScopes?(scopes: string[]): Promise<void>;
  suspended?: boolean;
  hideActions?: boolean;
  /** D99: when the cited conversation happened, in the list's own terms. */
  conversationAt?: { at: string; basis: "conversation" | "import" } | null;
}) {
  const t = useTranslate();
  const language = useUiLanguage();
  const [page, setPage] = useState<Page>("read");
  const [draft, setDraft] = useState(detail.memory.content);
  const [base, setBase] = useState(detail.memory.revision);
  const [conflict, setConflict] = useState<MemoryDetail | null>(null);
  const [conflictFound, setConflictFound] = useState(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState<UiMessage | "">("");
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
  const statusLabel =
    memory.status === "confirmed"
      ? t("已沉淀")
      : memory.status === "deleted"
        ? t("已删除")
        : memory.status === "superseded"
          ? t("已被取代")
          : t("状态待核对");
  const source = sourceNameOr(sources, evidence?.source_id, t("来源待核对"));
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
    if (dirty && !window.confirm(t("放弃尚未保存的修改？"))) return;
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
      if (alive.current) setError("暂时无法读取新版本，草稿已保留。");
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
          setError("修改未提交；草稿已保留。请按提示修正后重试。");
        else setError("修改尚未确认，草稿已保留。请先刷新核对，避免重复提交。");
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
            ? "删除未提交。请核对这条记忆的状态后重试。"
            : "删除尚未确认。请重新核对记忆状态，不会自动扩大删除范围。",
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
            ? "撤销未提交。请按提示修正后重试。"
            : "撤销尚未确认，请先核对当前记忆版本，避免重复撤销。",
        );
    } finally {
      flight.current = false;
      if (alive.current) setBusy(false);
    }
  }
  const author =
    evidence?.written_by === "person" || evidence?.written_by === "user"
      ? t("你说")
      : evidence?.written_by === "agent" || evidence?.written_by === "assistant"
        ? t("助手原话")
        : t("作者待核对");
  const timestamp = clockStamp(evidence?.created_at, language);
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
          {statusLabel} · {t("第 {revision} 版", { revision: memory.revision })}
        </p>
        <h3>{heading}</h3>
        {body && <p className="selected-memory-body">{body}</p>}
        <p className="selected-memory-source">
          {t("来源 {name}", { name: source })} · {timestamp}
        </p>
        <p className="selected-memory-times">
          {t("对话时间")}{" "}
          {clockStamp(conversationAt?.at ?? evidence?.created_at, language)}
          {" · "}
          {t("沉淀时间")} {clockStamp(memory.updated_at, language)}
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
            {t("编辑")}
          </R6Button>
        )}
        <section className="selected-memory-evidence">
          <h4>{t("原始依据")}</h4>
          <small>
            {author} · {t("原对话 {time}：", { time: timestamp })}
          </small>
          <blockquote>
            {evidence?.available
              ? evidence.verbatim
              : t("这段原文当前不可查看。")}
          </blockquote>
          {!hideActions && (
            <R6Button
              onClick={(event) => {
                trigger.current = event.currentTarget;
                setPage("original");
              }}
            >
              {t("查看原文")}
            </R6Button>
          )}
        </section>
        {!hideActions && (
          <R6Button variant="quiet" onClick={() => setShowHistory((v) => !v)}>
            {t("版本记录")}
          </R6Button>
        )}
        {!hideActions && showHistory && (
          <ol className="selected-memory-history">
            {detail.history.map((v) => (
              <li key={v.revision}>
                <small>{t("第 {revision} 版", { revision: v.revision })}</small>
                <p>{v.content}</p>
              </li>
            ))}
          </ol>
        )}
        {!hideActions && (
          <details className="selected-memory-more">
            <summary>{t("更多管理")}</summary>
            {onSaveScopes && (
              <MemoryRecallScope
                detail={detail}
                busy={busy}
                onSave={onSaveScopes}
              />
            )}
            {detail.history.length > 1 && (
              <R6Button pending={busy} onClick={() => void govern("undo")}>
                {t("撤销上次修改")}
              </R6Button>
            )}
            <R6Button
              onClick={(event) => {
                trigger.current = event.currentTarget;
                request.current = null;
                setPage("delete");
              }}
            >
              {t("删除记忆")}
            </R6Button>
          </details>
        )}
        {error && page === "read" && <p role="alert">{t(error)}</p>}
      </article>
      {page !== "read" &&
        createPortal(
          <section
            className="selected-memory-page"
            hidden={suspended}
            aria-label={
              page === "edit"
                ? t("编辑记忆")
                : page === "original"
                  ? t("原始对话")
                  : t("删除记忆")
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
                  ? t("编辑记忆")
                  : page === "original"
                    ? t("原始对话")
                    : t("删除记忆")}
              </h1>
              {page === "original" && (
                <R6Button onClick={close}>{t("返回记忆")}</R6Button>
              )}
            </header>
            {page === "edit" ? (
              <div className="selected-editor">
                <p>{t("修改这条记忆，原始对话保持不变。")}</p>
                <textarea
                  aria-label={t("记忆内容")}
                  name="memory-content"
                  value={draft}
                  disabled={busy}
                  maxLength={1000000}
                  onChange={(e) => {
                    setDraft(e.target.value);
                    request.current = null;
                  }}
                />
                <p>{t("使用范围保持不变")}</p>
                {conflictFound && (
                  <section className="selected-conflict">
                    <h2>{t("已有新版本")}</h2>
                    <p>{t("你的草稿已保留，先查看新版本再决定如何修改。")}</p>
                    <R6Button
                      pending={busy}
                      onClick={() => void refreshConflict()}
                    >
                      {t("查看新版本")}
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
                          {t("基于此版本继续编辑")}
                        </R6Button>
                      </>
                    )}
                  </section>
                )}
                <div className="selected-page-actions">
                  <R6Button disabled={busy} onClick={close}>
                    {t("取消")}
                  </R6Button>
                  <R6Button
                    variant="primary"
                    pending={busy}
                    disabled={!draft.trim() || conflictFound}
                    onClick={() => void save()}
                  >
                    {t("保存修改")}
                  </R6Button>
                </div>
              </div>
            ) : page === "original" ? (
              <div className="selected-original">
                <small>
                  {source} · {timestamp}
                </small>
                <p>{author}</p>
                {detail.evidence.map((item) => (
                  <section key={item.evidence_id}>
                    <small>
                      {sourceNameOr(sources, item.source_id, t("来源待核对"))} ·{" "}
                      {item.written_by === "person"
                        ? t("你")
                        : item.written_by === "agent"
                          ? t("助手")
                          : t("作者待核对")}
                    </small>
                    <blockquote>
                      {item.available
                        ? item.verbatim
                        : t("来源权限已变化或原文不可用。")}
                    </blockquote>
                  </section>
                ))}
                <small>{t("对应记忆")}</small>
                <h2>{heading}</h2>
                <R6Button onClick={close}>{t("查看记忆")}</R6Button>
              </div>
            ) : (
              <div className="selected-delete">
                <h2>{t("删除这条记忆？")}</h2>
                <p>{heading}</p>
                <p>
                  {t("删除后不再用于新的召回，来源 App 中的原始对话不会删除。")}
                  <br />
                  {t("已经发送给应用的内容无法远程收回。")}
                </p>
                <div className="selected-page-actions">
                  <R6Button disabled={busy} onClick={close}>
                    {t("取消")}
                  </R6Button>
                  <R6Button
                    variant="primary"
                    pending={busy}
                    onClick={() => void remove()}
                  >
                    {t("删除记忆")}
                  </R6Button>
                </div>
              </div>
            )}
            {error && (
              <p className="selected-page-error" role="alert">
                {t(error)}
              </p>
            )}
          </section>,
          document.body,
        )}
    </>
  );
}
